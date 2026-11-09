"""Доступ к PostgreSQL.

Здесь только транспорт: соединения, транзакции и чтение.
Бизнес-логика (правила DQ, нормализация, загрузка) живёт в dq.py / transform.py /
load.py, поэтому весь объём работы выполняется set-based запросами, а не
построчными операциями из Python.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import psycopg
from psycopg import Connection

from .config import Settings


def connect(settings: Settings, autocommit: bool = False) -> Connection:
    """Новое соединение. Ответственность за закрытие — на вызывающем коде."""
    return psycopg.connect(settings.dsn, autocommit=autocommit)


@contextmanager
def transaction(settings: Settings) -> Iterator[Connection]:
    """Транзакция: либо всё, либо ничего (нужно для атомарной перезаписи окна)."""
    with psycopg.connect(settings.dsn) as conn:
        with conn.transaction():
            yield conn


def fetch_all(conn: Connection, query: str, params: Sequence | None = None) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def fetch_scalar(conn: Connection, query: str, params: Sequence | None = None):
    rows = fetch_all(conn, query, params)
    return rows[0][0] if rows else None
