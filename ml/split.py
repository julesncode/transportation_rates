"""Хронологическое разбиение train/test.

Границы заданы в ТЗ относительно даты снимка as_of, а не системной даты:
    train: [as_of - 3 месяца, as_of - 3 недели)
    test:  [as_of - 3 недели, as_of]
Граница as_of - 3 недели входит только в test. Случайного перемешивания нет:
модель не должна видеть сделки того же периода, на котором её оценивают.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

import pandas as pd

TRAIN_MONTHS_BACK = 3
TEST_DAYS_BACK = 21


def subtract_months(value: date, months: int) -> date:
    """Сдвиг на месяцы с прижатием дня к концу месяца (31.03 - 1 мес = 28/29.02)."""
    year = value.year + (value.month - 1 - months) // 12
    month = (value.month - 1 - months) % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def resolve_windows(as_of: date) -> tuple[date, date, date]:
    """(начало train, начало test, конец test) — все границы вычисляются из as_of."""
    return subtract_months(as_of, TRAIN_MONTHS_BACK), as_of - timedelta(days=TEST_DAYS_BACK), as_of


def split_frame(
    frame: pd.DataFrame, train_start: date, test_start: date, as_of: date
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Разбить по дате погрузки. Пересечения окон не бывает по построению."""
    dates = pd.to_datetime(frame["loading_date"]).dt.date
    train = frame[(dates >= train_start) & (dates < test_start)]
    test = frame[(dates >= test_start) & (dates <= as_of)]
    return train.copy(), test.copy()
