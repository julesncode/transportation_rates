"""Обучение модели прогнозирования ставки.

Две модели:
* CatBoostRegressor — основная (нативная работа с категориями, не требует
  масштабирования и импутации);
* Ridge в Pipeline(OneHot + imputer + scaler) — линейный бейзлайн для сравнения.

Всё обучение идёт строго на train-выборке: импьютеры, скейлеры и кодировщики
настраиваются внутри Pipeline на train, границы отсечения выбросов определяются
по train (здесь — только флаг outlier_mark из источника).
"""

from __future__ import annotations

import json
import logging
import os
import time

import pandas as pd
from catboost import CatBoostRegressor
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from etl.config import REPO_ROOT, Settings
from etl.db import connect
from etl.logging_setup import log_event

from . import features, split
from .metrics import evaluate, format_metrics

logger = logging.getLogger("ml.train")

ARTIFACTS_DIR = REPO_ROOT / "artifacts"
MODEL_PATH = ARTIFACTS_DIR / "catboost_price.cbm"
METRICS_PATH = ARTIFACTS_DIR / "ml_metrics.json"

CATBOOST_PARAMS = dict(
    iterations=500,
    depth=8,
    learning_rate=0.06,
    loss_function="RMSE",
    verbose=False,
    allow_writing_files=False,
)


def build_catboost(settings: Settings) -> CatBoostRegressor:
    return CatBoostRegressor(random_seed=settings.random_seed, **CATBOOST_PARAMS)


def build_ridge(settings: Settings) -> Pipeline:
    """Линейный бейзлайн: требует кодирования и масштабирования, в отличие от CatBoost."""
    preprocessor = ColumnTransformer(
        transformers=[
            (
                "num",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scaler", StandardScaler()),
                    ]
                ),
                features.NUMERIC_FEATURES,
            ),
            (
                "cat",
                Pipeline(
                    [
                        (
                            "imputer",
                            SimpleImputer(
                                strategy="constant", fill_value=features.UNKNOWN_CATEGORY
                            ),
                        ),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                features.CATEGORICAL_FEATURES,
            ),
        ]
    )
    return Pipeline(
        [("preprocessor", preprocessor), ("model", Ridge(alpha=1.0, random_state=settings.random_seed))]
    )


def _atomic_replace(source, target) -> None:
    """Атомарно заменить файл.

    Пишем во временный файл и переименовываем: параллельный скоринг никогда не
    прочитает недописанную модель, а существующий файл другого владельца не
    блокирует запись (переименование требует прав на каталог, а не на файл).
    """
    os.replace(source, target)


def run_training(settings: Settings) -> dict:
    """Обучить обе модели и вернуть сводку метрик."""
    started = time.perf_counter()
    train_start, test_start, as_of = split.resolve_windows(settings.as_of)

    with connect(settings) as conn:
        frame = features.load_training_frame(conn, train_start, as_of)

    if frame.empty:
        raise SystemExit("Нет данных для обучения: сначала выполните load")

    frame = features.prepare(frame)
    train_frame, test_frame = split.split_frame(frame, train_start, test_start, as_of)
    if train_frame.empty or test_frame.empty:
        raise SystemExit(
            f"Пустая выборка: train={len(train_frame)}, test={len(test_frame)} "
            f"(окна {train_start}..{test_start}..{as_of})"
        )

    X_train, y_train = features.feature_matrix(train_frame)
    X_test, y_test = features.feature_matrix(test_frame)

    log_event(
        logger,
        "train:dataset",
        train_start=str(train_start),
        test_start=str(test_start),
        as_of=str(as_of),
        train_rows=len(X_train),
        test_rows=len(X_test),
    )

    catboost = build_catboost(settings)
    catboost.fit(X_train, y_train, cat_features=features.CATEGORICAL_FEATURES)
    catboost_metrics = evaluate(y_test, catboost.predict(X_test))
    log_event(logger, "train:catboost", metrics={"summary": format_metrics(catboost_metrics)})

    ridge = build_ridge(settings)
    ridge.fit(X_train, y_train)
    ridge_metrics = evaluate(y_test, ridge.predict(X_test))
    log_event(logger, "train:ridge", metrics={"summary": format_metrics(ridge_metrics)})

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    model_tmp = MODEL_PATH.with_name(MODEL_PATH.name + ".tmp")
    catboost.save_model(str(model_tmp))
    _atomic_replace(model_tmp, MODEL_PATH)

    payload = {
        "as_of": str(as_of),
        "train_start": str(train_start),
        "test_start": str(test_start),
        "train_rows": len(X_train),
        "test_rows": len(X_test),
        "random_seed": settings.random_seed,
        "catboost": catboost_metrics,
        "ridge_baseline": ridge_metrics,
        "features": features.FEATURES,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    metrics_tmp = METRICS_PATH.with_name(METRICS_PATH.name + ".tmp")
    metrics_tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _atomic_replace(metrics_tmp, METRICS_PATH)
    log_event(logger, "train:done", **{"duration_ms": payload["duration_ms"], "model_path": str(MODEL_PATH)})
    return payload
