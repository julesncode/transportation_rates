"""Правила качества данных (Вопрос 1).

Правила применяются в фиксированном порядке — первое сработавшее определяет
код карантина. Это делает статистику отсева воспроизводимой между прогонами:
одна и та же строка всегда получает один и тот же код.

Порядок и обоснование:

1. ``PRICE_NON_POSITIVE``   — цена <= 0: ставки нет вовсе;
2. ``DISTANCE_NON_POSITIVE``— дистанция <= 0: маршрут не определён, руб./км не считается;
3. ``NEGATIVE_WEIGHT``      — отрицательный вес: физически невозможно;
4. ``NEGATIVE_VOLUME``      — отрицательный объём: физически невозможно;
5. ``UNKNOWN_CITY``         — города нет в справочнике: сделка не привязывается к маршруту;
6. ``UNSUPPORTED_CURRENCY`` — валюта вне 643/840/978;
7. ``MISSING_EXCHANGE_RATE``— валютная сделка без точного курса на nearest_rate_date
                              (fallback по loading_date запрещён ТЗ);
8. ``PRICE_BELOW_RUB_FLOOR``— price_rub <= 1000: неправдоподобно дешёвый рейс.
                              Применяется к рублёвой ставке после конвертации,
                              поэтому одинаково работает для всех валют (решение Q2).

Отдельно: признак ``outlier_mark`` НЕ является причиной карантина. Такие строки
остаются в core, но исключаются из рыночной витрины и из обучения — так требует ТЗ.
"""

from __future__ import annotations

import logging

from psycopg import Connection

from .logging_setup import log_event

logger = logging.getLogger("etl.dq")

# Не влияет ли построчная проверка на is_fact: карантин применяется ко всем
# сделкам, включая ставки торгов (price_type = 6), чтобы не терять статистику.
CLASSIFY_SQL = """
CREATE TEMP TABLE _stg_prices ON COMMIT DROP AS
WITH base AS (
    SELECT p.id                 AS price_id,
           p.loading_date,
           p.loading_city_gcid,
           p.unloading_city_gcid,
           p.distance,
           p.total_weight,
           p.total_volume,
           p.body_type,
           p.price,
           p.currency_code,
           p.price_type,
           p.is_fact,
           p.complex,
           p.organization_id,
           p.outlier_mark,
           p.nearest_rate_date,
           p.end_loading_date,
           p.created_at,
           -- Для RUB курс не нужен: цена уже в рублях. Для 840/978 берём
           -- точный курс на nearest_rate_date, без подстановки соседних дат.
           CASE WHEN p.currency_code = '643' THEN 1.0 ELSE er.value END AS exchange_rate,
           EXISTS (SELECT 1 FROM raw.cities c WHERE c.gc_id = p.loading_city_gcid)   AS loading_city_known,
           EXISTS (SELECT 1 FROM raw.cities c WHERE c.gc_id = p.unloading_city_gcid) AS unloading_city_known
    FROM raw.prices p
    -- Курс берём точечно и детерминированно: в источнике (num_code, setting_date)
    -- НЕ уникальны, и обычный JOIN размножил бы сделку, уронив вставку в core
    -- на первичном ключе. При дублях побеждает последняя загруженная запись.
    LEFT JOIN LATERAL (
        SELECT er.value
        FROM raw.exchange_rates er
        WHERE er.num_code = p.currency_code
          AND er.setting_date = p.nearest_rate_date
        ORDER BY er.id DESC
        LIMIT 1
    ) er ON true
    WHERE p.loading_date >= %s
      AND p.loading_date <  %s
), conv AS (
    SELECT b.*,
           CASE WHEN b.exchange_rate IS NOT NULL
                THEN round((b.price * b.exchange_rate)::numeric, 2)::double precision
           END AS price_rub
    FROM base b
), cat AS (
    SELECT c.*,
           core.weight_category(c.total_weight) AS weight_category,
           core.volume_category(c.total_volume) AS volume_category
    FROM conv c
)
SELECT ct.*,
       CASE
           WHEN ct.price <= 0                                 THEN 'PRICE_NON_POSITIVE'
           WHEN ct.distance IS NULL OR ct.distance <= 0       THEN 'DISTANCE_NON_POSITIVE'
           WHEN ct.total_weight < 0                           THEN 'NEGATIVE_WEIGHT'
           WHEN ct.total_volume < 0                           THEN 'NEGATIVE_VOLUME'
           WHEN NOT (ct.loading_city_known AND ct.unloading_city_known) THEN 'UNKNOWN_CITY'
           WHEN ct.currency_code NOT IN ('643', '840', '978') THEN 'UNSUPPORTED_CURRENCY'
           WHEN ct.currency_code <> '643' AND ct.exchange_rate IS NULL THEN 'MISSING_EXCHANGE_RATE'
           WHEN ct.price_rub IS NULL OR ct.price_rub <= 1000  THEN 'PRICE_BELOW_RUB_FLOOR'
       END AS reason_code
FROM cat ct
"""


def classify(conn: Connection, start: str, end: str) -> int:
    """Построить staging-таблицу окна с рассчитанными полями и кодом карантина."""
    with conn.cursor() as cur:
        cur.execute(CLASSIFY_SQL, (start, end))
        cur.execute("ANALYZE _stg_prices")
        cur.execute("SELECT count(*) FROM _stg_prices")
        total = cur.fetchone()[0]
    log_event(logger, "dq:classified", window_start=start, window_end=end, rows=total)
    return total


def reason_stats(conn: Connection) -> list[tuple[str, int]]:
    """Распределение карантина по причинам — попадает в лог и в отчёты."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT reason_code, count(*) FROM _stg_prices "
            "WHERE reason_code IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
        )
        return [(row[0], row[1]) for row in cur.fetchall()]
