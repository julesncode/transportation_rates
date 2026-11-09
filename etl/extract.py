"""Извлечение: источник -> слой raw.

Копия нужна, чтобы повторный прогон не зависел от изменений в операционной БД
и чтобы витрину можно было воспроизвести из снимка. Справочники копируются
целиком (они малы), факты — только за окно.
"""

from __future__ import annotations

from psycopg import Connection

from .logging_setup import log_event
import logging

logger = logging.getLogger("etl.extract")

# Справочники: полная перезапись, они маленькие и меняются редко.
_REFERENCE_TABLES = ("cities", "body_types", "exchange_rates")


def copy_reference_tables(conn: Connection) -> dict[str, int]:
    """Полная перезапись справочников raw из public."""
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in _REFERENCE_TABLES:
            cur.execute(f"DELETE FROM raw.{table}")
            cur.execute(f"INSERT INTO raw.{table} SELECT * FROM public.{table}")
            cur.execute(f"SELECT count(*) FROM raw.{table}")
            counts[table] = cur.fetchone()[0]
    log_event(logger, "extract:references", **counts)
    return counts


def copy_window(conn: Connection, start: str, end: str) -> dict[str, int]:
    """Перезапись окна фактов в raw и связанных дочерних таблиц.

    Дочерние таблицы чистим по идентификаторам источника, а не по raw: так
    удаление остаётся корректным, даже если raw.prices уже частично изменён.
    """
    counts: dict[str, int] = {}
    window_child_sql = (
        "SELECT id FROM public.prices WHERE loading_date >= %s AND loading_date < %s"
    )
    with conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM raw.price_dates WHERE price_id IN ({window_child_sql})",
            (start, end),
        )
        cur.execute(
            f"DELETE FROM raw.price_body_types WHERE price_id IN ({window_child_sql})",
            (start, end),
        )
        cur.execute(
            "DELETE FROM raw.prices WHERE loading_date >= %s AND loading_date < %s",
            (start, end),
        )
        cur.execute(
            "INSERT INTO raw.prices SELECT * FROM public.prices "
            "WHERE loading_date >= %s AND loading_date < %s",
            (start, end),
        )
        cur.execute("SELECT count(*) FROM raw.prices WHERE loading_date >= %s AND loading_date < %s",
                    (start, end))
        counts["prices"] = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO raw.price_dates SELECT pd.* FROM public.price_dates pd "
            "WHERE pd.price_id IN (SELECT id FROM public.prices "
            "                      WHERE loading_date >= %s AND loading_date < %s)",
            (start, end),
        )
        cur.execute("SELECT count(*) FROM raw.price_dates WHERE price_id IN "
                    "(SELECT id FROM public.prices WHERE loading_date >= %s AND loading_date < %s)",
                    (start, end))
        counts["price_dates"] = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO raw.price_body_types SELECT pbt.* FROM public.price_body_types pbt "
            "WHERE pbt.price_id IN (SELECT id FROM public.prices "
            "                        WHERE loading_date >= %s AND loading_date < %s)",
            (start, end),
        )
        cur.execute("SELECT count(*) FROM raw.price_body_types WHERE price_id IN "
                    "(SELECT id FROM public.prices WHERE loading_date >= %s AND loading_date < %s)",
                    (start, end))
        counts["price_body_types"] = cur.fetchone()[0]

    log_event(logger, "extract:window", start=start, end=end, **counts)
    return counts
