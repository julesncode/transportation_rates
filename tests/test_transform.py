"""Трансформации: категории, группы кузова, дедупликация городов, развёртка."""

from __future__ import annotations

from datetime import date

import pytest
from psycopg import Connection

from etl import pipeline
from etl.config import Settings

from .helpers import add_price, add_price_body_type, add_price_dates


def _load(settings: Settings) -> dict:
    return pipeline.run_load(settings)


@pytest.mark.parametrize(
    ("weight", "expected"),
    [
        (30.0, "G20T"),
        (29.96, "G20T"),   # округление до 0.1 т поднимает границу
        (29.94, "FTL"),
        (10.0, "FTL"),
        (9.96, "FTL"),
        (9.94, "10T"),
        (5.0, "10T"),
        (4.96, "10T"),
        (2.5, "5T"),
        (2.44, "L5T"),
        (0.5, "L5T"),
        (0.44, "OTH"),
        (0.0, "OTH"),
    ],
)
def test_weight_category_boundaries(conn: Connection, weight: float, expected: str) -> None:
    value = conn.execute("SELECT core.weight_category(%s)", (weight,)).fetchone()[0]
    assert value == expected


@pytest.mark.parametrize(
    ("volume", "expected"),
    [(0.0, "OTH"), (60.0, "FTL"), (120.0, "FTL"), (120.1, "OTH")],
)
def test_volume_category_boundaries(conn: Connection, volume: float, expected: str) -> None:
    value = conn.execute("SELECT core.volume_category(%s)", (volume,)).fetchone()[0]
    assert value == expected


def test_cities_dedup_chooses_min_id(conn: Connection) -> None:
    """gc_id не уникален: вью должно вернуть одну строку — с минимальным id."""
    conn.execute("DELETE FROM raw.cities")
    first = conn.execute(
        "INSERT INTO raw.cities (full_name_local, full_name_gc, gc_id, name, latitude, longitude) "
        "VALUES (%s, %s, 1003, 'Казань', 55.7961, 49.1064) RETURNING id",
        ("Казань, Республика Татарстан", "Казань, Республика Татарстан"),
    ).fetchone()[0]
    second = conn.execute(
        "INSERT INTO raw.cities (full_name_local, full_name_gc, gc_id, name, latitude, longitude) "
        "VALUES (%s, %s, 1003, 'Казань', 55.7961, 49.1064) RETURNING id",
        ("Казань г, Татарстан", "Казань г, Татарстан"),
    ).fetchone()[0]

    rows = conn.execute(
        "SELECT city_id, city_name, region FROM core.cities_dedup WHERE gc_id = 1003"
    ).fetchall()
    assert len(rows) == 1
    city_id, city_name, region = rows[0]
    assert first < second
    assert city_id == first      # минимальный id из двух
    assert (city_name, region) == ("Казань", "Республика Татарстан")


def test_body_group_two_groups_from_price_body_types(conn: Connection, settings: Settings) -> None:
    price_id = add_price(conn, body_type=56)
    add_price_body_type(conn, price_id, 56)
    add_price_body_type(conn, price_id, 15)
    _load(settings)

    groups = {row[0] for row in conn.execute("SELECT body_group FROM core.price_body_group")}
    assert groups == {"Тент", "Борт"}


def test_body_group_dedup_inside_one_group(conn: Connection, settings: Settings) -> None:
    """56 и 57 — одна группа «Тент»: дубля быть не должно."""
    price_id = add_price(conn, body_type=56)
    add_price_body_type(conn, price_id, 56)
    add_price_body_type(conn, price_id, 57)
    _load(settings)

    rows = conn.execute("SELECT body_group, count(*) FROM core.price_body_group GROUP BY 1").fetchall()
    assert rows == [("Тент", 1)]


def test_body_group_fallback_to_price_body_type(conn: Connection, settings: Settings) -> None:
    """Нет связи в price_body_types — берём prices.body_type."""
    add_price(conn, body_type=4)
    _load(settings)

    group = conn.execute("SELECT body_group FROM core.price_body_group").fetchone()[0]
    assert group == "Рефрижератор"


def test_body_group_unknown(conn: Connection, settings: Settings) -> None:
    add_price(conn, body_type=None)
    _load(settings)

    group = conn.execute("SELECT body_group, std_id FROM core.price_body_group").fetchone()
    assert group == ("Не указан", None)


def test_price_dates_expand_to_months(conn: Connection, settings: Settings) -> None:
    price_id = add_price(conn, loading_date=date(2026, 9, 15), end_loading_date=date(2026, 12, 15))
    add_price_dates(conn, price_id, date(2026, 9, 15), date(2026, 12, 15))
    _load(settings)

    dates = [row[0] for row in conn.execute("SELECT price_date FROM core.price_date ORDER BY 1")]
    assert [d.month for d in dates] == [9, 10, 11, 12]


def test_price_km_is_derived(conn: Connection, settings: Settings) -> None:
    add_price(conn, price=80_000.0, distance=800.0)
    _load(settings)

    price_rub, price_km = conn.execute("SELECT price_rub, price_km FROM core.price_fact").fetchone()
    assert price_rub == 80_000.0
    assert price_km == 100.0
