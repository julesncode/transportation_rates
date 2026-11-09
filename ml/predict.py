"""Пакетный скоринг (batch inference).

Скорим два периода одним проходом:
* отложенный тестовый — там есть фактическая ставка, считается ошибка;
* плановые будущие заявки (`loading_date > as_of`) — фактические поля NULL,
  даже если в исходной строке заполнено price: на момент прогноза состоявшейся
  сделки ещё нет.

Охват: только строки, прошедшие DQ (они и есть core), `is_fact`, прямые рейсы,
положительная дистанция. Ставки торгов и составные маршруты не скорим: модель
на них не обучалась, а их «факт» не сопоставим с целевой переменной.
"""

from __future__ import annotations

import logging
import time
from datetime import date

import numpy as np
import pandas as pd
import psycopg
from catboost import CatBoostRegressor

from etl.config import Settings
from etl.db import connect, transaction
from etl.logging_setup import log_event

from . import features, split
from .train import MODEL_PATH

logger = logging.getLogger("ml.predict")

MODEL_NAME = "catboost_price_v1"

SELECT_SCORE_SQL = """
SELECT price_id, loading_date, loading_city_gcid, unloading_city_gcid,
       origin_city, origin_region, dest_city, dest_region,
       origin_lat, origin_lon, dest_lat, dest_lon,
       distance, total_weight, total_volume,
       body_group, weight_category, volume_category,
       price_type, price_rub
FROM core.feature_view
WHERE is_fact
  AND NOT complex
  AND distance > 0
  -- Нижняя граница — начало тестового окна; верхней нет: сюда попадают и
  -- плановые заявки из будущего.
  AND loading_date >= %s
ORDER BY loading_date, price_id
"""

UPSERT_SQL = """
INSERT INTO mart.ml_price_predictions (
    price_id, model_name, loading_date, loading_city_gcid, unloading_city_gcid,
    body_group, actual_price_rub, predicted_price_rub, abs_error_rub, ape_pct, scored_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (price_id, model_name) DO UPDATE
   SET loading_date        = EXCLUDED.loading_date,
       loading_city_gcid   = EXCLUDED.loading_city_gcid,
       unloading_city_gcid = EXCLUDED.unloading_city_gcid,
       body_group          = EXCLUDED.body_group,
       actual_price_rub    = EXCLUDED.actual_price_rub,
       predicted_price_rub = EXCLUDED.predicted_price_rub,
       abs_error_rub       = EXCLUDED.abs_error_rub,
       ape_pct             = EXCLUDED.ape_pct,
       scored_at           = now()
"""


def load_model() -> CatBoostRegressor:
    if not MODEL_PATH.exists():
        raise SystemExit(f"Модель не найдена: {MODEL_PATH}. Сначала выполните обучение (ml train).")
    model = CatBoostRegressor()
    model.load_model(str(MODEL_PATH))
    return model


def _none_if_nan(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    return float(value)


def build_prediction_rows(
    frame: pd.DataFrame, predictions: np.ndarray, as_of: date, model_name: str
) -> list[tuple]:
    """Собрать строки витрины прогнозов. Будущие заявки — без фактических полей."""
    dates = pd.to_datetime(frame["loading_date"]).dt.date
    is_future = dates > as_of
    actual = frame["price_rub"].astype(float).where(~is_future, np.nan)

    abs_error = (actual - pd.Series(predictions, index=frame.index)).abs()
    with np.errstate(divide="ignore", invalid="ignore"):
        ape = (abs_error / actual).abs() * 100.0
    ape = ape.where(actual > 0)

    rows: list[tuple] = []
    for position, (_, record) in enumerate(frame.iterrows()):
        rows.append(
            (
                int(record["price_id"]),
                model_name,
                record["loading_date"],
                int(record["loading_city_gcid"]),
                int(record["unloading_city_gcid"]),
                record["body_group"],
                _none_if_nan(actual.iloc[position]),
                float(predictions[position]),
                _none_if_nan(abs_error.iloc[position]),
                _none_if_nan(ape.iloc[position]),
            )
        )
    return rows


def run_batch_scoring(settings: Settings, model=None, model_name: str = MODEL_NAME) -> dict:
    """Скорить тестовый период и будущие заявки, записать прогнозы в БД."""
    started = time.perf_counter()
    _, test_start, as_of = split.resolve_windows(settings.as_of)
    model = model or load_model()

    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(SELECT_SCORE_SQL, (test_start,))
            columns = [desc.name for desc in cur.description]
            frame = pd.DataFrame(cur.fetchall(), columns=columns)

    if frame.empty:
        raise SystemExit("Нет сделок для скоринга")

    prepared = features.prepare(frame)
    predictions = np.asarray(model.predict(prepared[features.FEATURES]), dtype=float)
    rows = build_prediction_rows(prepared, predictions, as_of, model_name)

    with transaction(settings) as conn, conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows, returning=False)
        cur.execute(
            "SELECT count(*), count(actual_price_rub) FROM mart.ml_price_predictions "
            "WHERE model_name = %s",
            (model_name,),
        )
        total, with_actual = cur.fetchone()

    is_future = pd.to_datetime(prepared["loading_date"]).dt.date > as_of
    result = {
        "model_name": model_name,
        "test_rows": int((~is_future).sum()),
        "future_rows": int(is_future.sum()),
        "written": len(rows),
        "predictions_in_db": int(total),
        "with_actual_in_db": int(with_actual),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    log_event(logger, "predict:done", **result)
    return result
