"""Идемпотентность: повторный прогон за тот же период не задваивает данные."""

from __future__ import annotations

from datetime import date

from psycopg import Connection

from etl import pipeline
from etl.config import Settings

from .helpers import add_price, add_price_dates


def _counts(conn: Connection) -> tuple[int, int, int]:
    return (
        conn.execute("SELECT count(*) FROM core.price_fact").fetchone()[0],
        conn.execute("SELECT count(*) FROM dq.quarantine_prices").fetchone()[0],
        conn.execute("SELECT count(*) FROM core.price_body_group").fetchone()[0],
    )


def test_double_run_keeps_counts(conn: Connection, settings: Settings) -> None:
    add_price(conn, price=90_000.0)
    add_price(conn, price=0.0, loading_city_gcid=1003, unloading_city_gcid=1004)

    pipeline.run_load(settings)
    first = _counts(conn)
    pipeline.run_load(settings)
    second = _counts(conn)

    assert first == second == (1, 1, 1)


def test_rerun_updates_changed_value(conn: Connection, settings: Settings) -> None:
    price_id = add_price(conn, price=90_000.0)
    pipeline.run_load(settings)

    conn.execute("UPDATE public.prices SET price = 95_000 WHERE id = %s", (price_id,))
    pipeline.run_load(settings)

    rows = conn.execute("SELECT price_rub FROM core.price_fact").fetchall()
    assert rows == [(95_000.0,)]


def test_narrow_window_does_not_touch_other_days(conn: Connection, settings: Settings) -> None:
    """Перезапись ограничена окном: строки вне окна остаются нетронутыми."""
    add_price(conn, price=90_000.0, loading_date=date(2026, 9, 10))
    add_price(conn, price=80_000.0, loading_date=date(2026, 10, 10),
              loading_city_gcid=1003, unloading_city_gcid=1004)

    pipeline.run_load(settings)
    assert _counts(conn)[0] == 2

    pipeline.run_load(settings, start="2026-09-01", end="2026-10-01")
    assert _counts(conn)[0] == 2

    september = conn.execute(
        "SELECT count(*) FROM core.price_fact WHERE loading_date < '2026-10-01'"
    ).fetchone()[0]
    october = conn.execute(
        "SELECT count(*) FROM core.price_fact WHERE loading_date >= '2026-10-01'"
    ).fetchone()[0]
    assert (september, october) == (1, 1)


def test_price_dates_are_not_duplicated(conn: Connection, settings: Settings) -> None:
    price_id = add_price(conn, end_loading_date=date(2026, 11, 15))
    add_price_dates(conn, price_id, date(2026, 9, 15), date(2026, 11, 15))

    pipeline.run_load(settings)
    first = conn.execute("SELECT count(*) FROM core.price_date").fetchone()[0]
    pipeline.run_load(settings)
    second = conn.execute("SELECT count(*) FROM core.price_date").fetchone()[0]

    assert first == second == 3
