"""Помощники для тестов: минимальный источник и построение сделок.

Тесты работают с отдельной базой (см. conftest.py) и настоящими миграциями:
проверяется реальный путь raw -> dq -> core, а не изолированная функция.
"""

from __future__ import annotations

from datetime import date

from psycopg import Connection

# Даты, на которые в тестовом окружении есть курсы валют.
RATE_DATES = (date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3))

DEFAULT_CITIES = (
    (1001, "Москва, г. Москва", 55.7558, 37.6173),
    (1003, "Казань, Республика Татарстан", 55.7961, 49.1064),
    (1004, "Екатеринбург, Свердловская обл.", 56.8389, 60.6057),
)

DEFAULT_BODY_TYPES = (
    # id, name, id_from_1C, std_id
    (1, "бортовая", 1, 15),
    (2, "изотермический", 2, 2),
    (4, "рефрижератор", 4, 4),
    (15, "борт", 15, 15),
    (56, "тентованный", 56, 56),
    (57, "тент бок", 57, 56),
    (3, "контейнер", 3, 3),
)


def seed_reference(conn: Connection) -> None:
    """Справочники: города, кузова, курсы ЦБ на фиксированные даты."""
    for gc_id, full_name, lat, lon in DEFAULT_CITIES:
        conn.execute(
            "INSERT INTO public.cities (full_name_local, full_name_gc, gc_id, name, latitude, longitude) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (full_name, full_name, gc_id, full_name.split(",")[0].strip(), lat, lon),
        )
    for bt_id, name, id_from_1c, std_id in DEFAULT_BODY_TYPES:
        conn.execute(
            'INSERT INTO public.body_types (id, name, "id_from_1C", std_id) VALUES (%s, %s, %s, %s)',
            (bt_id, name, id_from_1c, std_id),
        )
    for day in RATE_DATES:
        for code, value in (("643", 1.0), ("840", 90.0), ("978", 100.0)):
            conn.execute(
                "INSERT INTO public.exchange_rates (num_code, value, setting_date) VALUES (%s, %s, %s)",
                (code, value, day),
            )


def add_city(conn: Connection, gc_id: int, full_name: str, lat: float = 55.0, lon: float = 37.0) -> int:
    row = conn.execute(
        "INSERT INTO public.cities (full_name_local, full_name_gc, gc_id, name, latitude, longitude) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (full_name, full_name, gc_id, full_name.split(",")[0].strip(), lat, lon),
    ).fetchone()
    return row[0]


def add_rate(conn: Connection, code: str, value: float, setting_date: date) -> None:
    conn.execute(
        "INSERT INTO public.exchange_rates (num_code, value, setting_date) VALUES (%s, %s, %s)",
        (code, value, setting_date),
    )


def add_price(
    conn: Connection,
    *,
    loading_date: date = date(2026, 9, 15),
    price: float = 90_000.0,
    loading_city_gcid: int = 1001,
    unloading_city_gcid: int = 1003,
    distance: float = 800.0,
    total_weight: float = 20.0,
    total_volume: float = 60.0,
    body_type: int | None = 56,
    currency_code: str = "643",
    price_type: int = 3,
    is_fact: bool = True,
    complex_: bool = False,
    outlier_mark: bool = False,
    nearest_rate_date: date | None = None,
    end_loading_date: date | None = None,
    organization_id: int | None = 1,
) -> int:
    """Добавить сделку в источник. Значения по умолчанию дают валидную RUB-строку."""
    row = conn.execute(
        """
        INSERT INTO public.prices (
            loading_date, loading_city_gcid, unloading_city_gcid, distance,
            total_weight, total_volume, body_type, price, currency_code,
            price_type, is_fact, complex, outlier_mark, nearest_rate_date,
            end_loading_date, organization_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            loading_date, loading_city_gcid, unloading_city_gcid, distance,
            total_weight, total_volume, body_type, price, currency_code,
            price_type, is_fact, complex_, outlier_mark, nearest_rate_date,
            end_loading_date, organization_id,
        ),
    ).fetchone()
    return row[0]


def add_price_body_type(conn: Connection, price_id: int, body_type_id: int) -> None:
    conn.execute(
        "INSERT INTO public.price_body_types (price_id, body_type_id) VALUES (%s, %s)",
        (price_id, body_type_id),
    )


def add_price_dates(conn: Connection, price_id: int, loading_date: date, end: date | None = None) -> int:
    """Помесячная развёртка как в источнике: месяцы от loading_date до end."""
    end = end or loading_date
    current = date(loading_date.year, loading_date.month, 1)
    last = date(end.year, end.month, 1)
    count = 0
    while current <= last:
        # В источнике даты идут «сдвигом на месяцы» от loading_date; для тестов
        # достаточно уникальной даты внутри каждого месяца.
        value = date(current.year, current.month, min(loading_date.day, 28))
        conn.execute(
            "INSERT INTO public.price_dates (price_id, price_date) VALUES (%s, %s)",
            (price_id, value),
        )
        count += 1
        current = date(current.year + (current.month == 12), (current.month % 12) + 1, 1)
    return count
