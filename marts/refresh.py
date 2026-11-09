"""Обновление витрин.

Порядок шага:
1) записать параметры прогона (as_of, окно свежести) в mart.pipeline_params —
   materialized view читает их во время пересчёта;
2) пересчитать витрину через REFRESH MATERIALIZED VIEW CONCURRENTLY (без
   блокировки читателей);
3) снять контрольную сводку до и после — она нужна для проверки идемпотентности.

REFRESH ... CONCURRENTLY нельзя выполнять внутри транзакции, поэтому шаг идёт
на autocommit-соединении.
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

import psycopg

from etl.config import Settings
from etl.logging_setup import log_event

logger = logging.getLogger("marts.refresh")

VIEW = "mart.mart_market_corridor"

UPDATE_PARAMS_SQL = """
INSERT INTO mart.pipeline_params (id, as_of, freshness_window_days, run_id, updated_at)
VALUES (true, %s, %s, %s::uuid, now())
ON CONFLICT (id) DO UPDATE
   SET as_of = EXCLUDED.as_of,
       freshness_window_days = EXCLUDED.freshness_window_days,
       run_id = EXCLUDED.run_id,
       updated_at = now()
"""

SNAPSHOT_SQL = """
SELECT count(*)                                                       AS cells,
       coalesce(sum(trips_count), 0)::bigint                          AS trips,
       coalesce(sum(carriers_count), 0)::bigint                       AS carriers,
       coalesce(round(sum(median_trip)::numeric, 2), 0)::float8       AS median_sum
FROM {view}
"""


def update_params(conn: psycopg.Connection, settings: Settings, run_id: str) -> None:
    conn.execute(UPDATE_PARAMS_SQL, (settings.as_of, settings.freshness_window_days, run_id))


def snapshot(conn: psycopg.Connection) -> dict[str, object]:
    """Контрольная сводка витрины (для логов и проверки идемпотентности)."""
    exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (VIEW,)).fetchone()[0]
    if not exists:
        return {"cells": None, "trips": None, "carriers": None, "median_sum": None}
    row = conn.execute(SNAPSHOT_SQL.format(view=VIEW)).fetchone()
    return {"cells": row[0], "trips": row[1], "carriers": row[2], "median_sum": float(row[3])}


def run_refresh(settings: Settings, run_id: str | None = None) -> dict[str, object]:
    """Обновить параметры и пересчитать витрину. Возвращает сводку до/после."""
    run_id = run_id or str(uuid4())
    started = time.perf_counter()

    with psycopg.connect(settings.dsn, autocommit=True) as conn:
        before = snapshot(conn)
        update_params(conn, settings, run_id)
        conn.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {VIEW}")
        # Статистика для BI-запросов: без неё планировщик недооценивает объём.
        conn.execute(f"ANALYZE {VIEW}")
        after = snapshot(conn)

    duration_ms = int((time.perf_counter() - started) * 1000)
    log_event(
        logger,
        "marts:refreshed",
        run_id=run_id,
        as_of=settings.as_of.isoformat(),
        freshness_window_days=settings.freshness_window_days,
        before=before,
        after=after,
        duration_ms=duration_ms,
    )
    return {"run_id": run_id, "before": before, "after": after, "duration_ms": duration_ms}
