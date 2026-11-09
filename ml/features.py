"""Признаковое пространство модели.

Правила:
* только свойства заявки на момент её создания (расстояние, груз, география,
  календарь) — ничего, что вычислено из целевой ставки;
* категориальные признаки отдаются CatBoost как есть (cat_features), без
  ручного кодирования: он умеет работать со строками и редкими категориями;
* циклические компоненты дат считаются здесь, чтобы train и predict видели
  ровно одну и ту же формулу.

Запрещённые признаки перечислены явно (FORBIDDEN): это и документ, и защита —
набор FEATURES собирается из разрешённых списков, а тест проверяет, что
пересечения нет.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from psycopg import Connection

TARGET = "price_rub"

# Признаки, которые запрещено использовать: производные тарифа, коммерческие
# компоненты и постфактум-агрегаты (price_group_stats считаются по сырой цене
# и включают саму предсказываемую сделку).
FORBIDDEN_FEATURES: frozenset[str] = frozenset(
    {
        "price",
        "price_rub",
        "price_km",
        "price_with_vat",
        "price_group_id",
        "avg_price",
        "min_km_price",
        "max_km_price",
        "customer_cost",
        "transportation_cost",
        "profit",
        "carrier_cost",
        "abs_error_rub",
        "ape_pct",
    }
)

NUMERIC_FEATURES: list[str] = [
    "distance",
    "total_weight",
    "total_volume",
    "origin_lat",
    "origin_lon",
    "dest_lat",
    "dest_lon",
    "year",
    "month",
    "day_of_week",
    "day_of_year",
    "month_sin",
    "month_cos",
    "dow_sin",
    "dow_cos",
    "doy_sin",
    "doy_cos",
]

CATEGORICAL_FEATURES: list[str] = [
    "origin_region",
    "dest_region",
    "origin_city",
    "dest_city",
    "body_group",
    "weight_category",
    "volume_category",
]

FEATURES: list[str] = NUMERIC_FEATURES + CATEGORICAL_FEATURES

UNKNOWN_CATEGORY = "Не указан"

SELECT_TRAIN_SQL = """
SELECT price_id, loading_date, origin_city, origin_region, dest_city, dest_region,
       origin_lat, origin_lon, dest_lat, dest_lon,
       distance, total_weight, total_volume,
       body_group, weight_category, volume_category,
       price_type, price_rub
FROM core.feature_view
WHERE is_fact
  AND NOT complex
  AND price_rub > 0
  AND distance > 0
  -- Базовая очистка таргета: размеченные ночным джобом выбросы в обучение не идут.
  AND NOT outlier_mark
  AND loading_date >= %s
  AND loading_date <= %s
ORDER BY loading_date, price_id
"""


def load_training_frame(conn: Connection, start: date, end: date) -> pd.DataFrame:
    """Исторические фактические сделки за окно обучения."""
    with conn.cursor() as cur:
        cur.execute(SELECT_TRAIN_SQL, (start, end))
        columns = [desc.name for desc in cur.description]
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=columns)


def add_time_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Календарные и гармонические признаки из даты погрузки."""
    frame = frame.copy()
    dates = pd.to_datetime(frame["loading_date"])
    frame["year"] = dates.dt.year.astype(int)
    frame["month"] = dates.dt.month.astype(int)
    frame["day_of_week"] = dates.dt.dayofweek.astype(int) + 1  # 1..7
    frame["day_of_year"] = dates.dt.dayofyear.astype(int)

    two_pi = 2 * np.pi
    frame["month_sin"] = np.sin(two_pi * frame["month"] / 12)
    frame["month_cos"] = np.cos(two_pi * frame["month"] / 12)
    frame["dow_sin"] = np.sin(two_pi * frame["day_of_week"] / 7)
    frame["dow_cos"] = np.cos(two_pi * frame["day_of_week"] / 7)
    # Год длиннее 365 дней — среднее значение 365.25 сохраняет непрерывность.
    frame["doy_sin"] = np.sin(two_pi * frame["day_of_year"] / 365.25)
    frame["doy_cos"] = np.cos(two_pi * frame["day_of_year"] / 365.25)
    return frame


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """Добавить временные признаки и нормализовать категориальные значения."""
    frame = add_time_features(frame)
    for column in CATEGORICAL_FEATURES:
        frame[column] = frame[column].fillna(UNKNOWN_CATEGORY).astype(str)
    return frame


def feature_matrix(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """X и y. Падает, если в признаки попал запрещённый столбец."""
    leaked = FORBIDDEN_FEATURES.intersection(FEATURES)
    if leaked:
        raise RuntimeError(f"В признаки попали запрещённые столбцы: {sorted(leaked)}")
    return frame[FEATURES].copy(), frame[TARGET].astype(float)
