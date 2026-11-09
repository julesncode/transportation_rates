-- 002: слой raw — побайтовые копии таблиц источника.
-- Схема структуры через LIKE: состав колонок повторяет источник, внешние ключи не копируются.
-- Заполнение raw выполняет ETL (etl/load.py) до применения правил DQ.
-- DEFAULT из источника (nextval) сохраняется, но мы всегда вставляем id явно.

CREATE TABLE IF NOT EXISTS raw.prices (
    LIKE public.prices INCLUDING ALL
);

CREATE TABLE IF NOT EXISTS raw.price_dates (
    LIKE public.price_dates INCLUDING ALL
);

CREATE TABLE IF NOT EXISTS raw.price_body_types (
    LIKE public.price_body_types INCLUDING ALL
);

CREATE TABLE IF NOT EXISTS raw.cities (
    LIKE public.cities INCLUDING ALL
);

CREATE TABLE IF NOT EXISTS raw.body_types (
    LIKE public.body_types INCLUDING ALL
);

CREATE TABLE IF NOT EXISTS raw.exchange_rates (
    LIKE public.exchange_rates INCLUDING ALL
);

-- Индексы, которых нет в источнике, но которые нужны нашим запросам.
CREATE INDEX IF NOT EXISTS raw_price_dates_price_date_idx ON raw.price_dates (price_date);
CREATE INDEX IF NOT EXISTS raw_prices_loading_date_idx ON raw.prices (loading_date);
