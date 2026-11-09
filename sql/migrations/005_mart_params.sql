-- 005: параметры прогона для витрин.
-- Materialized view не принимает параметров, поэтому as_of хранится в таблице:
-- шаг refresh сначала обновляет параметры, затем пересчитывает витрину.
-- Одна строка гарантируется булевым первичным ключом.

CREATE TABLE IF NOT EXISTS mart.pipeline_params (
    id                      boolean     PRIMARY KEY DEFAULT true CHECK (id),
    as_of                   date        NOT NULL,
    freshness_window_days   integer     NOT NULL CHECK (freshness_window_days >= 0),
    run_id                  uuid,
    updated_at              timestamptz NOT NULL DEFAULT now()
);

INSERT INTO mart.pipeline_params (id, as_of, freshness_window_days)
VALUES (true, DATE '2026-10-26', 30)
ON CONFLICT (id) DO NOTHING;
