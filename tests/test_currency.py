"""Конвертация валют и порог 1000 ₽ (решение Q2: порог по price_rub после конвертации)."""

from __future__ import annotations

from datetime import date

from psycopg import Connection

from etl import pipeline
from etl.config import Settings

from .helpers import add_price, add_rate


def _load(settings: Settings) -> dict:
    return pipeline.run_load(settings)


def test_exact_rate_conversion(conn: Connection, settings: Settings) -> None:
    add_price(conn, currency_code="840", price=100.0, nearest_rate_date=date(2026, 9, 2))
    _load(settings)

    row = conn.execute(
        "SELECT exchange_rate, price, price_rub, price_km FROM core.price_fact"
    ).fetchone()
    rate, price, price_rub, price_km = row
    assert rate == 90.0
    assert price == 100.0
    assert price_rub == 9_000.0
    assert price_km == round(9_000.0 / 800.0, 6)


def test_no_fallback_to_loading_date(conn: Connection, settings: Settings) -> None:
    """Курс есть на loading_date, но nearest_rate_date пуст — строка идёт в карантин."""
    add_rate(conn, "840", 95.0, date(2026, 9, 15))
    add_price(
        conn,
        currency_code="840",
        price=1_000.0,
        nearest_rate_date=None,
        loading_date=date(2026, 9, 15),
    )
    _load(settings)

    reasons = dict(
        conn.execute("SELECT reason_code, count(*) FROM dq.quarantine_prices GROUP BY 1").fetchall()
    )
    assert reasons == {"MISSING_EXCHANGE_RATE": 1}
    assert conn.execute("SELECT count(*) FROM core.price_fact").fetchone()[0] == 0


def test_rub_does_not_require_rate_date(conn: Connection, settings: Settings) -> None:
    """Рублёвой сделке курс не нужен: конвертации нет, курс = 1."""
    add_price(conn, price=50_000.0, nearest_rate_date=None)
    _load(settings)

    rate, price_rub = conn.execute("SELECT exchange_rate, price_rub FROM core.price_fact").fetchone()
    assert rate == 1.0
    assert price_rub == 50_000.0


def test_floor_applies_after_conversion(conn: Connection, settings: Settings) -> None:
    """5 USD * 90 = 450 ₽ — ниже порога, хотя в долларах сумма положительная."""
    add_price(conn, currency_code="840", price=5.0, nearest_rate_date=date(2026, 9, 2))
    _load(settings)

    reasons = dict(
        conn.execute("SELECT reason_code, count(*) FROM dq.quarantine_prices GROUP BY 1").fetchall()
    )
    assert reasons == {"PRICE_BELOW_RUB_FLOOR": 1}


def test_duplicate_rate_rows_do_not_duplicate_fact(conn: Connection, settings: Settings) -> None:
    """Две записи курса на одну дату не должны размножить сделку.

    Уникальность (num_code, setting_date) схемой не гарантируется, поэтому курс
    выбирается точечно. Иначе JOIN дал бы две строки и вставка упала бы на PK.
    """
    add_price(conn, currency_code="840", price=100.0, nearest_rate_date=date(2026, 9, 2))
    add_rate(conn, "840", 95.0, date(2026, 9, 2))   # дубль с большим id побеждает

    _load(settings)

    assert conn.execute("SELECT count(*) FROM core.price_fact").fetchone()[0] == 1
    rate = conn.execute("SELECT exchange_rate FROM core.price_fact").fetchone()[0]
    assert rate == 95.0


def test_floor_boundary(conn: Connection, settings: Settings) -> None:
    """Ровно 1000 ₽ — карантин, 1000.01 ₽ — валидная строка."""
    add_price(conn, price=1_000.0)
    add_price(conn, price=1_000.01, loading_city_gcid=1003, unloading_city_gcid=1004)
    _load(settings)

    assert conn.execute("SELECT count(*) FROM core.price_fact").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM dq.quarantine_prices").fetchone()[0] == 1
    price_rub = conn.execute("SELECT price_rub FROM core.price_fact").fetchone()[0]
    assert price_rub == 1_000.01
