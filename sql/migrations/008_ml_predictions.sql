-- 008: витрина прогнозов «факт vs прогноз».
--
-- Ключ (price_id, model_name): повторный скоринг обновляет строку, а не плодит
-- дубли. Внешний ключ на core.price_fact с ON DELETE CASCADE гарантирует, что
-- при перезагрузке окна устаревшие прогнозы исчезают вместе со сделкой.

CREATE TABLE IF NOT EXISTS mart.ml_price_predictions (
    price_id            bigint           NOT NULL REFERENCES core.price_fact (price_id) ON DELETE CASCADE,
    model_name          varchar(64)      NOT NULL,
    loading_date        date             NOT NULL,
    loading_city_gcid   integer          NOT NULL,
    unloading_city_gcid integer          NOT NULL,
    body_group          varchar(16),
    actual_price_rub    double precision,
    predicted_price_rub double precision NOT NULL,
    abs_error_rub       double precision,
    ape_pct             double precision,
    scored_at           timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (price_id, model_name)
);

CREATE INDEX IF NOT EXISTS ml_price_predictions_loading_date_idx
    ON mart.ml_price_predictions (loading_date);
CREATE INDEX IF NOT EXISTS ml_price_predictions_route_idx
    ON mart.ml_price_predictions (loading_city_gcid, unloading_city_gcid);
