"""Метрики качества регрессии: R², MAPE, RMSE, MAE."""

from __future__ import annotations

import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


def evaluate(y_true, y_pred) -> dict[str, float]:
    """Полный набор метрик на отложенной выборке."""
    true = np.asarray(y_true, dtype=float)
    predicted = np.asarray(y_pred, dtype=float)

    mask = true != 0
    mape = float(np.mean(np.abs((true[mask] - predicted[mask]) / true[mask])) * 100.0)

    return {
        "r2": float(r2_score(true, predicted)),
        "mape_pct": mape,
        "rmse_rub": float(np.sqrt(mean_squared_error(true, predicted))),
        "mae_rub": float(mean_absolute_error(true, predicted)),
        "rows": int(true.size),
    }


def format_metrics(metrics: dict[str, float]) -> str:
    """Человекочитаемая строка для лога и README."""
    return (
        f"rows={metrics['rows']} "
        f"R2={metrics['r2']:.4f} "
        f"MAPE={metrics['mape_pct']:.2f}% "
        f"RMSE={metrics['rmse_rub']:.0f} ₽ "
        f"MAE={metrics['mae_rub']:.0f} ₽"
    )
