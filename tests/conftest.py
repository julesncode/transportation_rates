"""Общие фикстуры тестов.

Тестовая БД создаётся один раз за сессию из sql/schema.sql и всех миграций —
тесты проверяют реальную схему, а не упрощённую копию. Каждый тест получает
чистые таблицы.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
from collections.abc import Iterator

import psycopg
import pytest

from etl.config import MIGRATIONS_DIR, SOURCE_SQL_DIR, Settings, load_settings

from .helpers import seed_reference

TEST_DB_NAME = os.getenv("TEST_DB_NAME", "deals_test")


def _psql(settings: Settings, database: str, *args: str) -> None:
    env = os.environ.copy()
    env["PGPASSWORD"] = settings.db_password
    env["PGCLIENTENCODING"] = "UTF8"
    subprocess.run(
        [
            "psql",
            "-h", settings.db_host,
            "-p", str(settings.db_port),
            "-U", settings.db_user,
            "-d", database,
            "-v", "ON_ERROR_STOP=1",
            "-q",
            *args,
        ],
        env=env,
        check=True,
    )


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Настройки, указывающие на отдельную тестовую базу."""
    base = load_settings()
    test_settings = dataclasses.replace(base, db_name=TEST_DB_NAME, log_level="WARNING")

    _psql(base, "postgres", "-c", f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
    _psql(base, "postgres", "-c", f'CREATE DATABASE "{TEST_DB_NAME}"')

    _psql(test_settings, TEST_DB_NAME, "-f", str(SOURCE_SQL_DIR / "schema.sql"))
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        _psql(test_settings, TEST_DB_NAME, "-f", str(migration))
    return test_settings


@pytest.fixture()
def conn(settings: Settings) -> Iterator[psycopg.Connection]:
    """Чистое соединение с засеянными справочниками."""
    with psycopg.connect(settings.dsn, autocommit=True) as connection:
        connection.execute(
            "TRUNCATE public.prices, public.price_dates, public.price_body_types, "
            "public.cities, public.body_types, public.exchange_rates "
            "RESTART IDENTITY CASCADE"
        )
        connection.execute(
            "TRUNCATE core.price_fact, dq.quarantine_prices, "
            "raw.prices, raw.price_dates, raw.price_body_types, "
            "raw.cities, raw.body_types, raw.exchange_rates "
            "RESTART IDENTITY CASCADE"
        )
        seed_reference(connection)
        yield connection
