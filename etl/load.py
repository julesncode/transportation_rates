"""Загрузка в core и карантин.

Идемпотентность обеспечивается атомарной перезаписью окна: в одной транзакции
удаляются все ранее загруженные строки за интервал и вставляются новые.
Повторный прогон за тот же период не может задвоить ни факты, ни карантин.
"""

from __future__ import annotations

import logging

from psycopg import Connection

from .logging_setup import log_event

logger = logging.getLogger("etl.load")

DELETE_FACT_SQL = "DELETE FROM core.price_fact WHERE loading_date >= %s AND loading_date < %s"
DELETE_QUARANTINE_SQL = (
    "DELETE FROM dq.quarantine_prices WHERE loading_date >= %s AND loading_date < %s"
)

INSERT_QUARANTINE_SQL = """
INSERT INTO dq.quarantine_prices (
    price_id, reason_code, loading_date, loading_city_gcid, unloading_city_gcid,
    distance, total_weight, total_volume, price, currency_code, price_rub,
    price_type, is_fact, run_id
)
SELECT s.price_id, s.reason_code, s.loading_date, s.loading_city_gcid, s.unloading_city_gcid,
       s.distance, s.total_weight, s.total_volume, s.price, s.currency_code, s.price_rub,
       s.price_type, s.is_fact, %s::uuid
FROM _stg_prices s
WHERE s.reason_code IS NOT NULL
"""


def delete_window(conn: Connection, start: str, end: str) -> dict[str, int]:
    """Удалить ранее загруженные строки окна (facts каскадом снимает дочерние)."""
    with conn.cursor() as cur:
        cur.execute(DELETE_FACT_SQL, (start, end))
        deleted_facts = cur.rowcount
        cur.execute(DELETE_QUARANTINE_SQL, (start, end))
        deleted_quarantine = cur.rowcount
    log_event(
        logger,
        "load:window_deleted",
        window_start=start,
        window_end=end,
        price_fact=deleted_facts,
        quarantine=deleted_quarantine,
    )
    return {"price_fact": deleted_facts, "quarantine": deleted_quarantine}


def insert_quarantine(conn: Connection, run_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute(INSERT_QUARANTINE_SQL, (run_id,))
        inserted = cur.rowcount
    log_event(logger, "load:quarantine", rows=inserted)
    return inserted
