"""ML-признаки: циклическое кодирование, разбиение, отсутствие утечек."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from psycopg import Connection

from etl import pipeline
from etl.config import Settings
from ml import features, metrics, split

from .helpers import add_price, add_price_body_type


def test_cyclical_month_encoding() -> None:
    frame = pd.DataFrame({"loading_date": [date(2026, 3, 1), date(2026, 6, 1), date(2026, 12, 1)]})
    result = features.add_time_features(frame)

    assert result.loc[0, "month_sin"] == pytest.approx(1.0)
    assert result.loc[0, "month_cos"] == pytest.approx(0.0, abs=1e-9)
    assert result.loc[1, "month_sin"] == pytest.approx(0.0, abs=1e-9)
    assert result.loc[1, "month_cos"] == pytest.approx(-1.0)
    assert result.loc[2, "month_sin"] == pytest.approx(0.0, abs=1e-9)
    assert result.loc[2, "month_cos"] == pytest.approx(1.0)


def test_day_of_week_encoding() -> None:
    # 2026-10-26 — понедельник; нумерация 1..7 (пн = 1), в отличие от pandas dayofweek.
    frame = pd.DataFrame({"loading_date": [date(2026, 10, 26)]})
    result = features.add_time_features(frame)

    assert result.loc[0, "day_of_week"] == 1
    assert result.loc[0, "day_of_year"] == 299
    assert result.loc[0, "dow_sin"] == pytest.approx(np.sin(2 * np.pi * 1 / 7))
    assert result.loc[0, "dow_cos"] == pytest.approx(np.cos(2 * np.pi * 1 / 7))


def test_windows_are_derived_from_as_of() -> None:
    assert split.resolve_windows(date(2026, 10, 26)) == (
        date(2026, 7, 26),
        date(2026, 10, 5),
        date(2026, 10, 26),
    )


def test_month_shift_clamps_day() -> None:
    assert split.subtract_months(date(2026, 5, 31), 1) == date(2026, 4, 30)
    assert split.subtract_months(date(2026, 1, 15), 3) == date(2025, 10, 15)


def test_split_boundaries() -> None:
    """Граница 2026-10-05 входит только в test, 2026-07-26 — только в train."""
    frame = pd.DataFrame(
        {
            "loading_date": [
                date(2026, 7, 25),
                date(2026, 7, 26),
                date(2026, 10, 4),
                date(2026, 10, 5),
                date(2026, 10, 26),
                date(2026, 10, 27),
            ]
        }
    )
    train, test = split.split_frame(frame, *split.resolve_windows(date(2026, 10, 26)))

    assert list(train["loading_date"]) == [date(2026, 7, 26), date(2026, 10, 4)]
    assert list(test["loading_date"]) == [date(2026, 10, 5), date(2026, 10, 26)]


def test_no_forbidden_features_in_matrix() -> None:
    assert features.FORBIDDEN_FEATURES.isdisjoint(features.FEATURES)
    assert "price_rub" not in features.FEATURES
    assert "price_km" not in features.FEATURES
    assert "price_group_id" not in features.FEATURES


def test_metrics_values() -> None:
    result = metrics.evaluate([100.0, 200.0], [110.0, 180.0])

    assert result["mae_rub"] == pytest.approx(15.0)
    assert result["rmse_rub"] == pytest.approx(np.sqrt(250.0))
    assert result["mape_pct"] == pytest.approx(10.0)
    assert result["r2"] == pytest.approx(0.9)


def test_feature_view_has_one_row_per_price(conn: Connection, settings: Settings) -> None:
    price_id = add_price(conn, body_type=56)
    add_price_body_type(conn, price_id, 56)
    add_price_body_type(conn, price_id, 15)
    pipeline.run_load(settings)

    rows = conn.execute("SELECT body_group FROM core.feature_view").fetchall()
    assert rows == [("Борт",)]      # детерминированно: минимальный std_id


def test_feature_view_regions(conn: Connection, settings: Settings) -> None:
    add_price(conn, loading_city_gcid=1003, unloading_city_gcid=1004)
    pipeline.run_load(settings)

    row = conn.execute("SELECT origin_region, dest_region FROM core.feature_view").fetchone()
    assert row == ("Республика Татарстан", "Свердловская обл.")


def test_feature_view_exposes_no_target_derivatives(conn: Connection) -> None:
    cursor = conn.execute("SELECT * FROM core.feature_view LIMIT 0")
    columns = {desc.name for desc in cursor.description}

    assert "price_rub" in columns          # целевая переменная нужна явно
    assert "price_km" not in columns
    assert "price_group_id" not in columns
    assert "avg_price" not in columns


def test_training_frame_filters_rows(conn: Connection, settings: Settings) -> None:
    add_price(conn, price=100_000)                                   # валидная
    add_price(conn, price=200_000, outlier_mark=True)                # выброс
    add_price(conn, price=300_000, price_type=6, is_fact=False)      # ставка торгов
    add_price(conn, price=400_000, complex_=True)                    # составной маршрут
    pipeline.run_load(settings)

    frame = features.load_training_frame(conn, date(2026, 7, 1), date(2026, 10, 31))
    assert len(frame) == 1
    assert frame.iloc[0]["price_rub"] == 100_000
