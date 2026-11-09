"""Оркестрация шага загрузки: extract -> dq -> transform -> load.

Вся работа идёт в одной транзакции: либо окно перезаписано целиком, либо не
изменено вовсе. Промежуточное состояние, в котором карантин уже записан, а
факты ещё нет, невозможно.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from uuid import uuid4

from . import dq, extract, load, transform
from .config import Settings
from .db import connect, fetch_scalar, transaction
from .logging_setup import log_event

logger = logging.getLogger("etl.pipeline")


def resolve_window(settings: Settings, start: str | None, end: str | None) -> tuple[date, date]:
    """Окно загрузки.

    * явные `--start` / `--end` — как переданы;
    * `core` пуст (первый запуск) — вся история источника;
    * иначе — последние `ETL_WINDOW_DAYS` дней от максимальной загруженной даты.
      Перезапись окна идемпотентна, поэтому перекрытие с предыдущим запуском безопасно.
    """
    with connect(settings) as conn:
        min_date = fetch_scalar(conn, "SELECT min(loading_date) FROM public.prices")
        max_date = fetch_scalar(conn, "SELECT max(loading_date) FROM public.prices")
        loaded_to = fetch_scalar(conn, "SELECT max(loading_date) FROM core.price_fact")
    if min_date is None:
        raise SystemExit("Источник пуст: сначала выполните init-source")

    if start or end:
        window_start = date.fromisoformat(start) if start else min_date
        window_end = date.fromisoformat(end) if end else max_date + timedelta(days=1)
    elif loaded_to is None:
        log_event(logger, "window:initial", reason="core is empty")
        window_start, window_end = min_date, max_date + timedelta(days=1)
    else:
        window_start = max(loaded_to - timedelta(days=settings.etl_window_days), min_date)
        window_end = max_date + timedelta(days=1)

    if window_start >= window_end:
        raise SystemExit(f"Пустое окно: start={window_start} end={window_end}")
    return window_start, window_end


def run_load(settings: Settings, start: str | None = None, end: str | None = None) -> dict:
    """Загрузить окно в core и карантин. Возвращает счётчики прогона."""
    started = time.perf_counter()
    window_start, window_end = resolve_window(settings, start, end)
    run_id = str(uuid4())
    start_s, end_s = window_start.isoformat(), window_end.isoformat()

    log_event(
        logger,
        "load:start",
        run_id=run_id,
        window_start=start_s,
        window_end=end_s,
    )

    with transaction(settings) as conn:
        raw_counts = extract.copy_reference_tables(conn)
        raw_counts.update(extract.copy_window(conn, start_s, end_s))

        deleted = load.delete_window(conn, start_s, end_s)
        total = dq.classify(conn, start_s, end_s)
        quarantined = load.insert_quarantine(conn, run_id)
        facts = transform.insert_fact(conn, run_id)
        body_groups = transform.insert_body_groups(conn)
        price_dates = transform.insert_price_dates(conn)
        reasons = dq.reason_stats(conn)

    # Свежая база не имеет статистики по core, и планировщик выбирает плохой план
    # для core.feature_view: обучение на чистом окружении растягивалось с секунд
    # до минут. ANALYZE выполняется после коммита, чтобы видеть загруженные строки.
    # Ошибка ANALYZE не должна отменять уже закоммиченный прогон и уходить в ретрай.
    try:
        with connect(settings, autocommit=True) as conn:
            for table in (
                "core.price_fact",
                "core.price_body_group",
                "core.price_primary_group",
                "core.price_date",
            ):
                conn.execute(f"ANALYZE {table}")
    except Exception:  # noqa: BLE001 — данные загружены, статистика вторична
        logger.warning("load:analyze_failed", exc_info=True)

    duration_ms = int((time.perf_counter() - started) * 1000)
    result = {
        "run_id": run_id,
        "window_start": start_s,
        "window_end": end_s,
        "rows_read": total,
        "rows_loaded": facts,
        "rows_quarantined": quarantined,
        "body_groups": body_groups,
        "price_dates": price_dates,
        "deleted": deleted,
        "quarantine_by_reason": dict(reasons),
        "duration_ms": duration_ms,
    }
    log_event(logger, "load:done", **result)
    return result
