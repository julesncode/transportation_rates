"""Правила качества данных: каждое нарушение даёт свой код карантина."""

from __future__ import annotations

from datetime import date

from psycopg import Connection

from etl import pipeline
from etl.config import Settings

from .helpers import add_price


def _load(settings: Settings) -> dict:
    return pipeline.run_load(settings)


def _core_count(conn: Connection) -> int:
    return conn.execute("SELECT count(*) FROM core.price_fact").fetchone()[0]


def _quarantine(conn: Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT reason_code, count(*) FROM dq.quarantine_prices GROUP BY 1"
    ).fetchall()
    return dict(rows)


def test_price_non_positive(conn: Connection, settings: Settings) -> None:
    add_price(conn, price=0)
    result = _load(settings)

    assert result["rows_quarantined"] == 1
    assert _quarantine(conn) == {"PRICE_NON_POSITIVE": 1}
    assert _core_count(conn) == 0


def test_distance_non_positive(conn: Connection, settings: Settings) -> None:
    add_price(conn, distance=0)
    _load(settings)

    assert _quarantine(conn) == {"DISTANCE_NON_POSITIVE": 1}
    assert _core_count(conn) == 0


def test_negative_weight(conn: Connection, settings: Settings) -> None:
    add_price(conn, total_weight=-1.0)
    _load(settings)

    assert _quarantine(conn) == {"NEGATIVE_WEIGHT": 1}


def test_negative_volume(conn: Connection, settings: Settings) -> None:
    add_price(conn, total_volume=-1.0)
    _load(settings)

    assert _quarantine(conn) == {"NEGATIVE_VOLUME": 1}


def test_unknown_city(conn: Connection, settings: Settings) -> None:
    add_price(conn, unloading_city_gcid=9999)
    _load(settings)

    assert _quarantine(conn) == {"UNKNOWN_CITY": 1}


def test_unsupported_currency(conn: Connection, settings: Settings) -> None:
    add_price(conn, currency_code="999", price=90_000)
    _load(settings)

    assert _quarantine(conn) == {"UNSUPPORTED_CURRENCY": 1}


def test_missing_exchange_rate_without_rate_date(conn: Connection, settings: Settings) -> None:
    add_price(conn, currency_code="840", price=1_000, nearest_rate_date=None)
    _load(settings)

    assert _quarantine(conn) == {"MISSING_EXCHANGE_RATE": 1}


def test_missing_exchange_rate_when_date_has_no_rate(conn: Connection, settings: Settings) -> None:
    # 2026-09-05 — воскресенье, курса ЦБ на эту дату в фикстуре нет.
    add_price(conn, currency_code="840", price=1_000, nearest_rate_date=date(2026, 9, 5))
    _load(settings)

    assert _quarantine(conn) == {"MISSING_EXCHANGE_RATE": 1}


def test_rule_priority_first_match_wins(conn: Connection, settings: Settings) -> None:
    """Строка нарушает два правила сразу — код должен быть детерминирован."""
    add_price(conn, price=0, distance=0, total_weight=-5.0)
    _load(settings)

    assert _quarantine(conn) == {"PRICE_NON_POSITIVE": 1}


def test_outlier_is_not_quarantined(conn: Connection, settings: Settings) -> None:
    """outlier_mark — флаг для витрины/ML, а не причина отбраковки."""
    add_price(conn, price=900_000, outlier_mark=True)
    _load(settings)

    assert _core_count(conn) == 1
    assert _quarantine(conn) == {}


def test_non_fact_rows_reach_core(conn: Connection, settings: Settings) -> None:
    """Ставки торгов (price_type = 6) не отбраковываются: фильтр is_fact — задача витрины."""
    add_price(conn, price_type=6, is_fact=False)
    _load(settings)

    assert _core_count(conn) == 1
    value = conn.execute("SELECT is_fact FROM core.price_fact").fetchone()[0]
    assert value is False
