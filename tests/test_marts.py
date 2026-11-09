"""Витрина коридора: зерно, фильтры, метрики, надёжность, идемпотентность refresh."""

from __future__ import annotations

from datetime import date

import pytest
from psycopg import Connection

from etl import pipeline
from etl.config import Settings
from marts import refresh

from .helpers import add_price, add_price_body_type, add_price_dates


def _add(conn: Connection, *, loading_date: date = date(2026, 9, 15), end: date | None = None, **kwargs) -> int:
    """Сделка + её помесячная развёртка: без price_dates витрина её не увидит."""
    price_id = add_price(conn, loading_date=loading_date, end_loading_date=end, **kwargs)
    add_price_dates(conn, price_id, loading_date, end)
    return price_id


def _build(conn: Connection, settings: Settings) -> None:
    pipeline.run_load(settings)
    refresh.run_refresh(settings)


def _cells(conn: Connection) -> list[tuple]:
    return conn.execute(
        "SELECT month, body_group, weight_category, trips_count, carriers_count, "
        "       p25_trip, median_trip, p75_trip, avg_trip, cv_rate, mom_pct, yoy_pct, reliability_level "
        "FROM mart.mart_market_corridor ORDER BY month, body_group"
    ).fetchall()


def test_grain_is_unique(conn: Connection, settings: Settings) -> None:
    for price in (100_000, 110_000, 120_000):
        _add(conn, price=price)
    _build(conn, settings)

    duplicates = conn.execute(
        "SELECT count(*) FROM ("
        "  SELECT month, loading_city_gcid, unloading_city_gcid, body_group, weight_category"
        "  FROM mart.mart_market_corridor"
        "  GROUP BY 1, 2, 3, 4, 5 HAVING count(*) > 1"
        ") t"
    ).fetchone()[0]
    assert duplicates == 0


def test_filters_exclude_unwanted_rows(conn: Connection, settings: Settings) -> None:
    _add(conn, price=100_000)                                        # валидная
    _add(conn, price=100_000, price_type=6, is_fact=False)           # ставка торгов
    _add(conn, price=100_000, complex_=True)                         # составной маршрут
    _add(conn, price=900_000, outlier_mark=True)                     # выброс
    _build(conn, settings)

    rows = _cells(conn)
    assert len(rows) == 1
    assert rows[0][3] == 1


def test_future_months_are_excluded(conn: Connection, settings: Settings) -> None:
    """Заявка после as_of не создаёт ячеек; её же месяцы в прошлом — создают."""
    _add(conn, loading_date=date(2026, 11, 10), price=100_000)
    _add(conn, loading_date=date(2026, 10, 10), end=date(2026, 12, 15), price=200_000)
    _build(conn, settings)

    months = [row[0] for row in _cells(conn)]
    assert months == [date(2026, 10, 1)]


def test_percentiles_and_average(conn: Connection, settings: Settings) -> None:
    for price in (100_000, 200_000, 300_000, 400_000):
        _add(conn, price=price)
    _build(conn, settings)

    row = _cells(conn)[0]
    p25, median, p75, avg = row[5], row[6], row[7], row[8]
    assert p25 == pytest.approx(175_000)
    assert median == pytest.approx(250_000)
    assert p75 == pytest.approx(325_000)
    assert avg == pytest.approx(250_000)


def test_carriers_count_only_for_carrier_price_types(conn: Connection, settings: Settings) -> None:
    _add(conn, price=100_000, price_type=3)   # перевозчик, организация 1
    _add(conn, price=110_000, price_type=9)   # прямой заказ, организация 1
    _add(conn, price=120_000, price_type=1)   # заявка заказчика, организация 1
    _build(conn, settings)

    row = _cells(conn)[0]
    assert row[3] == 3          # три сделки в ячейке
    assert row[4] == 1          # но перевозчик один


def test_cv_rate_requires_two_observations(conn: Connection, settings: Settings) -> None:
    _add(conn, price=100_000)
    _build(conn, settings)
    assert _cells(conn)[0][9] is None

    _add(conn, price=120_000)
    _build(conn, settings)
    cv_rate = _cells(conn)[0][9]
    assert cv_rate is not None and cv_rate > 0


def test_mom_pct_compares_calendar_previous_month(conn: Connection, settings: Settings) -> None:
    _add(conn, loading_date=date(2026, 8, 10), price=100_000)
    _add(conn, loading_date=date(2026, 9, 10), price=110_000)
    _build(conn, settings)

    rows = {row[0]: row for row in _cells(conn)}
    assert rows[date(2026, 8, 1)][10] is None          # предыдущего месяца нет
    assert rows[date(2026, 9, 1)][10] == pytest.approx(10.0)


def test_mom_is_null_when_previous_month_missing(conn: Connection, settings: Settings) -> None:
    """Июль и сентябрь без августа: подставлять июль как «предыдущий» нельзя."""
    _add(conn, loading_date=date(2026, 7, 10), price=100_000)
    _add(conn, loading_date=date(2026, 9, 10), price=120_000)
    _build(conn, settings)

    rows = {row[0]: row for row in _cells(conn)}
    assert rows[date(2026, 9, 1)][10] is None


def test_yoy_pct(conn: Connection, settings: Settings) -> None:
    _add(conn, loading_date=date(2025, 9, 10), price=100_000)
    _add(conn, loading_date=date(2026, 9, 10), price=120_000)
    _build(conn, settings)

    rows = {row[0]: row for row in _cells(conn)}
    assert rows[date(2026, 9, 1)][11] == pytest.approx(20.0)
    assert rows[date(2025, 9, 1)][11] is None


def test_reliability_levels(conn: Connection, settings: Settings) -> None:
    # Свежий месяц (сентябрь 2026, конец месяца внутри окна 30 дней) -> HIGH
    for i in range(30):
        _add(conn, price=100_000 + i * 1_000, organization_id=100 + i % 5, price_type=3)
    # Август 2026: объём есть, но месяц не пересекается с окном -> MEDIUM (решение Q4)
    for i in range(30):
        _add(conn, loading_date=date(2026, 8, 10), price=95_000 + i * 1_000,
             organization_id=150 + i % 5, price_type=3,
             loading_city_gcid=1004, unloading_city_gcid=1001)
    # Старый месяц (июнь 2025), те же 30 сделок и 5 перевозчиков -> MEDIUM
    for i in range(30):
        _add(conn, loading_date=date(2025, 6, 10), price=90_000 + i * 1_000,
             organization_id=200 + i % 5, price_type=3,
             loading_city_gcid=1003, unloading_city_gcid=1004)
    # Мало данных -> LOW_DATA
    _add(conn, price=100_000, loading_city_gcid=1003, unloading_city_gcid=1001)
    _build(conn, settings)

    rows = conn.execute(
        "SELECT month, loading_city_gcid, unloading_city_gcid, trips_count, carriers_count, "
        "       reliability_level FROM mart.mart_market_corridor"
    ).fetchall()
    cells = {(row[0], row[1], row[2]): row for row in rows}

    fresh = cells[(date(2026, 9, 1), 1001, 1003)]
    assert (fresh[3], fresh[4], fresh[5]) == (30, 5, "HIGH")

    august = cells[(date(2026, 8, 1), 1004, 1001)]
    assert (august[3], august[4], august[5]) == (30, 5, "MEDIUM")

    old = cells[(date(2025, 6, 1), 1003, 1004)]
    assert (old[3], old[4], old[5]) == (30, 5, "MEDIUM")

    rare = cells[(date(2026, 9, 1), 1003, 1001)]
    assert (rare[3], rare[5]) == (1, "LOW_DATA")


def test_high_requires_five_carriers(conn: Connection, settings: Settings) -> None:
    """30 сделок, но всего 4 перевозчика — HIGH не полагается."""
    for i in range(30):
        _add(conn, price=100_000 + i * 1_000, organization_id=100 + i % 4, price_type=3)
    _build(conn, settings)

    row = _cells(conn)[0]
    assert row[3] == 30
    assert row[4] == 4
    assert row[12] == "MEDIUM"


def test_month_comes_from_price_dates(conn: Connection, settings: Settings) -> None:
    _add(conn, loading_date=date(2026, 9, 15), end=date(2026, 10, 15), price=100_000)
    _build(conn, settings)

    months = [row[0] for row in _cells(conn)]
    assert months == [date(2026, 9, 1), date(2026, 10, 1)]


def test_multi_body_group_creates_separate_cells(conn: Connection, settings: Settings) -> None:
    price_id = _add(conn, price=100_000, body_type=56)
    add_price_body_type(conn, price_id, 56)
    add_price_body_type(conn, price_id, 15)
    _build(conn, settings)

    groups = sorted(row[1] for row in _cells(conn))
    assert groups == ["Борт", "Тент"]
    assert all(row[3] == 1 for row in _cells(conn))


def test_duplicate_price_dates_in_month_count_once(conn: Connection, settings: Settings) -> None:
    """Две price_dates в одном месяце не должны взвешивать сделку дважды.

    Без схлопывания перцентили считались бы по [100k, 100k, 300k],
    и медиана уехала бы на 100k вместо 200k.
    """
    first = _add(conn, loading_date=date(2026, 9, 5), price=100_000)
    conn.execute(
        "INSERT INTO public.price_dates (price_id, price_date) VALUES (%s, %s)",
        (first, date(2026, 9, 20)),
    )
    _add(conn, loading_date=date(2026, 9, 10), price=300_000)
    _build(conn, settings)

    row = conn.execute(
        "SELECT trips_count, p25_trip, median_trip, p75_trip, last_price_date "
        "FROM mart.mart_market_corridor"
    ).fetchone()
    assert row[0] == 2                       # две сделки, а не три строки
    assert row[1] == pytest.approx(150_000)
    assert row[2] == pytest.approx(200_000)
    assert row[3] == pytest.approx(250_000)
    assert row[4] == date(2026, 9, 20)       # последняя дата месяца


def test_last_price_date_reflects_actual_data(conn: Connection, settings: Settings) -> None:
    """Справочная колонка показывает дату последней сделки в ячейке."""
    _add(conn, loading_date=date(2026, 9, 10), price=100_000)
    _add(conn, loading_date=date(2026, 9, 20), price=110_000)
    _build(conn, settings)

    value = conn.execute("SELECT last_price_date FROM mart.mart_market_corridor").fetchone()[0]
    assert value == date(2026, 9, 20)


def test_refresh_is_idempotent(conn: Connection, settings: Settings) -> None:
    _add(conn, price=100_000)
    _add(conn, price=150_000, loading_date=date(2026, 8, 10))
    _build(conn, settings)
    first = refresh.snapshot(conn)

    refresh.run_refresh(settings)
    second = refresh.snapshot(conn)

    assert first == second
