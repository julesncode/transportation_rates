"""Единая точка входа пайплайна.

    python -m etl.cli init-source   # создать БД-источник: schema.sql + synthetic.sql
    python -m etl.cli migrate       # применить sql/migrations/*.sql по порядку
    python -m etl.cli check         # проверка окружения: схемы, счётчики, параметры
    python -m etl.cli load          # ETL: raw -> core + quarantine (этап S1)
    python -m etl.cli marts         # refresh витрин (этап S3)
    python -m etl.cli ml            # обучение и скоринг (этапы S4-S5)

Один и тот же код вызывается вручную, из тестов и из Airflow — оркестратор
не содержит собственной логики.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from .config import MIGRATIONS_DIR, SOURCE_SQL_DIR, Settings, load_settings
from .db import connect, fetch_all, fetch_scalar
from .logging_setup import log_event, setup_logging

logger = logging.getLogger("etl.cli")


# --------------------------------------------------------------------------- #
# Общие помощники
# --------------------------------------------------------------------------- #
def _psql_base(settings: Settings) -> list[str]:
    return [
        "psql",
        "-h",
        settings.db_host,
        "-p",
        str(settings.db_port),
        "-U",
        settings.db_user,
        "-d",
        settings.db_name,
        "-v",
        "ON_ERROR_STOP=1",
        "-q",
    ]


def _run_psql(settings: Settings, args: list[str]) -> None:
    env = os.environ.copy()
    env["PGPASSWORD"] = settings.db_password
    env["PGCLIENTENCODING"] = "UTF8"
    log_event(logger, "psql:start", args=" ".join(args[1:]))
    subprocess.run(_psql_base(settings) + args, env=env, check=True)


def _source_state(settings: Settings) -> str:
    """Состояние источника: ready (ровно N_PRICES строк), partial, empty.

    Проверка «есть хотя бы одна строка» опасна: прерванная генерация оставляет
    неполный датасет, и следующий запуск молча на нём продолжит работу.
    """
    with connect(settings) as conn:
        if fetch_scalar(conn, "SELECT to_regclass('public.prices')") is None:
            return "empty"
        rows = fetch_scalar(conn, "SELECT count(*) FROM public.prices")
    return "ready" if rows == settings.n_prices else "partial"


# --------------------------------------------------------------------------- #
# Команды
# --------------------------------------------------------------------------- #
def cmd_init_source(args: argparse.Namespace, settings: Settings) -> None:
    """Сгенерировать источник. Повторный запуск безопасен, частичное состояние чинится."""
    state = _source_state(settings)
    if state == "ready" and not args.force:
        log_event(logger, "init_source:skipped", reason="source already loaded",
                  prices=settings.n_prices)
        return

    if state == "partial" or args.force:
        # Таблицы могли остаться от прерванного DDL/генерации: сносим схему целиком,
        # иначе schema.sql упадёт на уже существующих таблицах.
        log_event(logger, "init_source:drop", schema="public", state=state)
        _run_psql(settings, ["-c", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"])

    # --single-transaction: обрыв не оставляет ни половину таблиц, ни часть строк.
    _run_psql(settings, ["--single-transaction", "-f", str(SOURCE_SQL_DIR / "schema.sql")])
    _run_psql(
        settings,
        [
            "--single-transaction",
            "-v", f"n_prices={settings.n_prices}",
            "-f", str(SOURCE_SQL_DIR / "synthetic.sql"),
        ],
    )
    with connect(settings) as conn:
        rows = fetch_scalar(conn, "SELECT count(*) FROM public.prices")
    if rows != settings.n_prices:
        raise SystemExit(
            f"Источник сгенерирован неполностью: {rows} строк вместо {settings.n_prices}. "
            f"Повторите запуск — неполное состояние будет пересоздано."
        )
    log_event(logger, "init_source:done", prices=int(rows), n_prices=settings.n_prices)


def cmd_migrate(args: argparse.Namespace, settings: Settings) -> None:
    """Применить миграции DWH по возрастанию имени файла (DDL идемпотентен)."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise SystemExit(f"В {MIGRATIONS_DIR} нет миграций")
    for path in files:
        _run_psql(settings, ["-f", str(path)])
    log_event(logger, "migrate:done", applied=[p.name for p in files])


def cmd_check(args: argparse.Namespace, settings: Settings) -> None:
    """Диагностика окружения: соединение, схемы, объёмы, параметры прогона."""
    log_event(logger, "check:settings", **settings.safe_dict())
    with connect(settings) as conn:
        version = fetch_scalar(conn, "SHOW server_version")
        schemas = fetch_all(
            conn,
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name IN ('raw','core','dq','mart','ml') ORDER BY 1",
        )
        counts: dict[str, object] = {}
        for table in ("public.prices", "public.price_dates", "public.cities",
                      "raw.prices", "raw.price_dates", "raw.cities"):
            exists = fetch_scalar(conn, "SELECT to_regclass(%s)", (table,))
            counts[table] = fetch_scalar(conn, f"SELECT count(*) FROM {table}") if exists else None
    log_event(
        logger,
        "check:done",
        server_version=version,
        schemas=[row[0] for row in schemas],
        counts=counts,
    )


def cmd_marts(args: argparse.Namespace, settings: Settings) -> None:
    from marts.refresh import run_refresh

    run_refresh(settings)


def cmd_ml(args: argparse.Namespace, settings: Settings) -> None:
    from ml.predict import run_batch_scoring
    from ml.train import run_training

    run_training(settings)
    run_batch_scoring(settings)


def cmd_ml_train(args: argparse.Namespace, settings: Settings) -> None:
    from ml.train import run_training

    run_training(settings)


def cmd_ml_predict(args: argparse.Namespace, settings: Settings) -> None:
    from ml.predict import run_batch_scoring

    run_batch_scoring(settings)


def cmd_verify(args: argparse.Namespace, settings: Settings) -> None:
    """Финальная проверка: данные дошли до всех слоёв, витрины не пусты."""
    with connect(settings) as conn:
        facts = fetch_scalar(conn, "SELECT count(*) FROM core.price_fact") or 0
        cells = fetch_scalar(conn, "SELECT count(*) FROM mart.mart_market_corridor") or 0
        predictions = fetch_scalar(conn, "SELECT count(*) FROM mart.ml_price_predictions") or 0
        quarantine = fetch_scalar(conn, "SELECT count(*) FROM dq.quarantine_prices") or 0

    log_event(
        logger,
        "verify:counts",
        price_fact=facts,
        mart_cells=cells,
        predictions=predictions,
        quarantine=quarantine,
    )
    if facts == 0 or cells == 0 or predictions == 0:
        raise SystemExit(
            f"Пайплайн неполон: price_fact={facts}, mart_cells={cells}, predictions={predictions}"
        )


def cmd_ensure_airflow_db(args: argparse.Namespace, settings: Settings) -> None:
    """Создать базу метаданных Airflow, если её ещё нет (идемпотентно).

    Нужна, потому что встроенный postgres создаёт только одну базу при
    инициализации тома, а на уже существующем томе init-скрипты не выполняются.
    """
    import psycopg

    dsn = (
        f"host={settings.db_host} port={settings.db_port} dbname=postgres "
        f"user={settings.db_user} password={settings.db_password}"
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", ("airflow",)
        ).fetchone()
        if exists:
            log_event(logger, "airflow_db:exists", database="airflow")
            return
        conn.execute("CREATE DATABASE airflow")
        log_event(logger, "airflow_db:created", database="airflow")


def cmd_load(args: argparse.Namespace, settings: Settings) -> None:
    # Реализация появится в S1 (etl/pipeline.py). Импорт намеренно ленивый:
    # без него S0 остаётся запускаемым.
    from . import pipeline

    pipeline.run_load(settings, start=args.start, end=args.end)


# --------------------------------------------------------------------------- #
# Разбор аргументов
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m etl.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-source", help="создать БД-источник и сгенерировать сделки")
    p_init.add_argument("--force", action="store_true", help="пересоздать схему public")
    p_init.set_defaults(func=cmd_init_source)

    sub.add_parser("migrate", help="применить миграции DWH").set_defaults(func=cmd_migrate)
    sub.add_parser("check", help="проверить окружение и счётчики").set_defaults(func=cmd_check)

    p_load = sub.add_parser("load", help="ETL: raw -> core + quarantine")
    p_load.add_argument("--start", type=str, default=None, help="начало окна, YYYY-MM-DD")
    p_load.add_argument("--end", type=str, default=None, help="конец окна, YYYY-MM-DD")
    p_load.set_defaults(func=cmd_load)

    sub.add_parser("marts", help="refresh витрин").set_defaults(func=cmd_marts)
    sub.add_parser("ml", help="обучение и скоринг одной командой").set_defaults(func=cmd_ml)
    sub.add_parser("ml-train", help="только обучение модели").set_defaults(func=cmd_ml_train)
    sub.add_parser("ml-predict", help="только пакетный скоринг").set_defaults(func=cmd_ml_predict)
    sub.add_parser("verify", help="проверить полноту слоёв").set_defaults(func=cmd_verify)
    sub.add_parser(
        "ensure-airflow-db", help="создать БД метаданных Airflow, если её нет"
    ).set_defaults(func=cmd_ensure_airflow_db)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_level)
    try:
        args.func(args, settings)
    except Exception:  # noqa: BLE001 — логируем и отдаём ненулевой код
        logger.exception("command:failed", extra={"fields": {"command": args.command}})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
