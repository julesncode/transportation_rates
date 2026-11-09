"""Пакетный скоринг: охват, фактические поля, идемпотентность."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from psycopg import Connection

from etl import pipeline
from etl.config import Settings
from ml.predict import run_batch_scoring

from .helpers import add_price


class ConstantModel:
    """Заглушка вместо обученной модели: предсказывает одно и то же значение."""

    def __init__(self, value: float) -> None:
        self.value = value

    def predict(self, frame) -> np.ndarray:
        return np.full(len(frame), self.value, dtype=float)


def _predictions(conn: Connection, model_name: str = "stub") -> list[tuple]:
    return conn.execute(
        "SELECT price_id, loading_date, actual_price_rub, predicted_price_rub, "
        "       abs_error_rub, ape_pct FROM mart.ml_price_predictions "
        "WHERE model_name = %s ORDER BY price_id",
        (model_name,),
    ).fetchall()


def test_test_period_gets_actuals_and_errors(conn: Connection, settings: Settings) -> None:
    add_price(conn, loading_date=date(2026, 10, 10), price=100_000)
    pipeline.run_load(settings)

    run_batch_scoring(settings, model=ConstantModel(110_000), model_name="stub")

    row = _predictions(conn)[0]
    assert row[2] == pytest.approx(100_000)     # actual
    assert row[3] == pytest.approx(110_000)     # predicted
    assert row[4] == pytest.approx(10_000)      # abs error
    assert row[5] == pytest.approx(10.0)        # ape, %


def test_future_rows_have_no_actuals(conn: Connection, settings: Settings) -> None:
    """У плановой заявки price заполнен в источнике, но фактических полей быть не должно."""
    add_price(conn, loading_date=date(2026, 11, 10), price=150_000)
    pipeline.run_load(settings)

    run_batch_scoring(settings, model=ConstantModel(140_000), model_name="stub")

    row = _predictions(conn)[0]
    assert row[2] is None       # actual_price_rub
    assert row[4] is None       # abs_error_rub
    assert row[5] is None       # ape_pct


def test_scope_excludes_non_fact_and_complex(conn: Connection, settings: Settings) -> None:
    add_price(conn, loading_date=date(2026, 10, 10), price=100_000)                  # скорится
    add_price(conn, loading_date=date(2026, 10, 10), price=100_000,
              price_type=6, is_fact=False)                                          # ставка торгов
    add_price(conn, loading_date=date(2026, 10, 10), price=100_000, complex_=True)   # составной
    add_price(conn, loading_date=date(2026, 9, 10), price=100_000)                   # до test_start
    pipeline.run_load(settings)

    result = run_batch_scoring(settings, model=ConstantModel(100_000), model_name="stub")

    assert result["written"] == 1
    assert len(_predictions(conn)) == 1


def test_rerun_is_idempotent(conn: Connection, settings: Settings) -> None:
    add_price(conn, loading_date=date(2026, 10, 10), price=100_000)
    add_price(conn, loading_date=date(2026, 11, 10), price=150_000)
    pipeline.run_load(settings)

    run_batch_scoring(settings, model=ConstantModel(120_000), model_name="stub")
    first = _predictions(conn)
    run_batch_scoring(settings, model=ConstantModel(120_000), model_name="stub")
    second = _predictions(conn)

    assert len(second) == 2
    assert first == second


def test_model_name_is_recorded(conn: Connection, settings: Settings) -> None:
    add_price(conn, loading_date=date(2026, 10, 10), price=100_000)
    pipeline.run_load(settings)

    run_batch_scoring(settings, model=ConstantModel(90_000), model_name="catboost_price_v1")

    names = {row[0] for row in conn.execute("SELECT DISTINCT model_name FROM mart.ml_price_predictions")}
    assert names == {"catboost_price_v1"}
