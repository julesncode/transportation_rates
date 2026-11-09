"""Нормализация и запись в core.

Всё считается на множествах (INSERT ... SELECT по staging-таблице), без
построчных операций в Python: это ELT, а не row-by-row ETL.
"""

from __future__ import annotations

import logging

from psycopg import Connection

from .logging_setup import log_event

logger = logging.getLogger("etl.transform")

# Валидные строки окна -> core.price_fact. price_km считается здесь же,
# чтобы витрина и ML не расходились в формуле.
INSERT_FACT_SQL = """
INSERT INTO core.price_fact (
    price_id, loading_date, loading_city_gcid, unloading_city_gcid,
    distance, total_weight, total_volume, body_type,
    price, currency_code, exchange_rate, price_rub, price_km,
    weight_category, volume_category, price_type, is_fact, complex,
    organization_id, outlier_mark, nearest_rate_date, end_loading_date,
    created_at, run_id
)
SELECT s.price_id, s.loading_date, s.loading_city_gcid, s.unloading_city_gcid,
       s.distance, s.total_weight, s.total_volume, s.body_type,
       s.price, s.currency_code, s.exchange_rate, s.price_rub,
       s.price_rub / NULLIF(s.distance, 0) AS price_km,
       s.weight_category, s.volume_category, s.price_type, s.is_fact, s.complex,
       s.organization_id, s.outlier_mark, s.nearest_rate_date, s.end_loading_date,
       s.created_at, %s::uuid
FROM _stg_prices s
WHERE s.reason_code IS NULL
"""

# Помесячная развёртка: цена из price_dates, а не loading_date (Вопрос 2).
INSERT_PRICE_DATE_SQL = """
INSERT INTO core.price_date (price_id, price_date)
SELECT pd.price_id, pd.price_date
FROM raw.price_dates pd
JOIN _stg_prices s ON s.price_id = pd.price_id
WHERE s.reason_code IS NULL
"""

# Группы кузова: price_body_types -> std_id, при отсутствии связи fallback на
# prices.body_type, при отсутствии и его — 'Не указан'. Одна цена внутри одной
# группы остаётся одной строкой, но разные группы (тент + борт) сохраняются.
INSERT_BODY_GROUP_SQL = """
INSERT INTO core.price_body_group (price_id, body_group, std_id, source_body_type_id)
WITH valid AS (
    SELECT s.price_id, s.body_type
    FROM _stg_prices s
    WHERE s.reason_code IS NULL
), mapped AS (
    SELECT v.price_id, v.body_type AS source_body_type_id, bt.std_id
    FROM valid v
    JOIN raw.price_body_types pbt ON pbt.price_id = v.price_id
    JOIN raw.body_types bt        ON bt.id = pbt.body_type_id
    UNION
    SELECT v.price_id, v.body_type, bt.std_id
    FROM valid v
    LEFT JOIN raw.body_types bt ON bt.id = v.body_type
    WHERE NOT EXISTS (
        SELECT 1 FROM raw.price_body_types x WHERE x.price_id = v.price_id
    )
), grouped AS (
    SELECT m.price_id,
           core.body_group_name(m.std_id) AS body_group,
           m.std_id,
           m.source_body_type_id
    FROM mapped m
), complete AS (
    -- Сделка без единой распознанной группы всё равно должна попасть в витрину
    SELECT v.price_id,
           COALESCE(g.body_group, 'Не указан') AS body_group,
           g.std_id,
           v.body_type AS source_body_type_id
    FROM valid v
    LEFT JOIN grouped g ON g.price_id = v.price_id
)
SELECT price_id, body_group, min(std_id), min(source_body_type_id)
FROM complete
GROUP BY price_id, body_group
"""

# Первичная группа для ML: один детерминированный признак на сделку.
# Предвычисление убирает DISTINCT ON по всей таблице групп из запросов обучения.
INSERT_PRIMARY_GROUP_SQL = """
INSERT INTO core.price_primary_group (price_id, body_group, std_id)
SELECT DISTINCT ON (price_id)
       price_id,
       body_group,
       std_id
FROM core.price_body_group
WHERE price_id IN (SELECT price_id FROM _stg_prices WHERE reason_code IS NULL)
ORDER BY price_id, std_id NULLS LAST, body_group
ON CONFLICT (price_id) DO UPDATE
   SET body_group = EXCLUDED.body_group,
       std_id     = EXCLUDED.std_id
"""


def insert_fact(conn: Connection, run_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute(INSERT_FACT_SQL, (run_id,))
        inserted = cur.rowcount
    log_event(logger, "transform:price_fact", rows=inserted)
    return inserted


def insert_price_dates(conn: Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(INSERT_PRICE_DATE_SQL)
        inserted = cur.rowcount
    log_event(logger, "transform:price_date", rows=inserted)
    return inserted


def insert_body_groups(conn: Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(INSERT_BODY_GROUP_SQL)
        inserted = cur.rowcount
        cur.execute(INSERT_PRIMARY_GROUP_SQL)
    log_event(logger, "transform:body_group", rows=inserted)
    return inserted
