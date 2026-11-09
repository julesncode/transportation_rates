"""Конфигурация пайплайна.

Единственный источник значений — переменные окружения (файл .env при запуске
через docker-compose). Захардкоженных паролей и абсолютных путей нет:
пути строятся относительно корня репозитория.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# Корень репозитория: etl/config.py -> ../ = корень
REPO_ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = REPO_ROOT / "sql"
MIGRATIONS_DIR = SQL_DIR / "migrations"
# DDL источника и генератор лежат в sql/ (как в исходном пакете задания);
# дублировать их в sql/source/ нет смысла — это те же файлы.
SOURCE_SQL_DIR = SQL_DIR


class ConfigError(RuntimeError):
    """Некорректная конфигурация: лучше упасть сразу, чем работать с дефолтами."""


def _env(name: str) -> str:
    """Прочитать обязательную переменную.

    Значения по умолчанию заданы ТОЛЬКО в docker-compose.yml (якорь x-app-env).
    Дублировать их здесь нельзя: расхождение двух наборов даёт тихую подмену
    параметра, которую не видно ни в логах, ни в тестах.
    """
    value = os.getenv(name)
    if value is None or value == "":
        raise ConfigError(
            f"Не задана переменная окружения {name}. "
            f"Она приходит из docker-compose.yml (x-app-env) или из .env."
        )
    return value


def _env_int(name: str) -> int:
    raw = _env(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должно быть целым числом, получено: {raw!r}") from exc


def _env_date(name: str) -> date:
    raw = _env(name)
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ConfigError(f"{name} ожидается в формате YYYY-MM-DD, получено: {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Параметры одного прогона пайплайна."""

    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    as_of: date
    freshness_window_days: int
    etl_window_days: int
    n_prices: int
    random_seed: int
    log_level: str

    @property
    def dsn(self) -> str:
        """Строка подключения libpq. Пароль не логируется."""
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={self.db_user} password={self.db_password} connect_timeout=10"
        )

    def safe_dict(self) -> dict[str, object]:
        """Представление для логов: без пароля."""
        return {
            "db_host": self.db_host,
            "db_port": self.db_port,
            "db_name": self.db_name,
            "db_user": self.db_user,
            "as_of": self.as_of.isoformat(),
            "freshness_window_days": self.freshness_window_days,
            "etl_window_days": self.etl_window_days,
            "n_prices": self.n_prices,
            "random_seed": self.random_seed,
        }


def load_settings() -> Settings:
    """Собрать настройки из окружения (вызывается один раз на процесс).

    Все переменные обязательны: их значения по умолчанию заданы в compose,
    поэтому отсутствие любой из них означает ошибку конфигурации, а не повод
    работать с другим значением.
    """
    return Settings(
        db_host=_env("POSTGRES_HOST"),
        db_port=_env_int("POSTGRES_PORT"),
        db_name=_env("POSTGRES_DB"),
        db_user=_env("POSTGRES_USER"),
        db_password=_env("POSTGRES_PASSWORD"),
        as_of=_env_date("AS_OF_DATE"),
        freshness_window_days=_env_int("FRESHNESS_WINDOW_DAYS"),
        etl_window_days=_env_int("ETL_WINDOW_DAYS"),
        n_prices=_env_int("N_PRICES"),
        random_seed=_env_int("RANDOM_SEED"),
        log_level=_env("LOG_LEVEL").upper(),
    )
