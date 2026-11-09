-- 004: карантин отбракованных строк. Ни одна отбракованная запись не теряется:
-- сохраняются код причины, ключевые поля и прогон, который её отбраковал.

CREATE TABLE IF NOT EXISTS dq.reason_codes (
    code        varchar(32) PRIMARY KEY,
    description text        NOT NULL
);

INSERT INTO dq.reason_codes (code, description) VALUES
    ('PRICE_NON_POSITIVE',
     'Цена <= 0: отсутствие ставки или служебное значение'),
    ('DISTANCE_NON_POSITIVE',
     'Дистанция <= 0 или отсутствует: маршрут не определён'),
    ('NEGATIVE_WEIGHT',
     'Отрицательный вес груза: физически невозможно'),
    ('NEGATIVE_VOLUME',
     'Отрицательный объём груза: физически невозможно'),
    ('UNKNOWN_CITY',
     'Город отправления или назначения отсутствует в справочнике cities'),
    ('UNSUPPORTED_CURRENCY',
     'Код валюты вне поддерживаемого набора 643/840/978'),
    ('MISSING_EXCHANGE_RATE',
     'Для валютной сделки нет точного курса на nearest_rate_date (fallback по loading_date запрещён)'),
    ('PRICE_BELOW_RUB_FLOOR',
     'Ставка в рублях <= 1000: неправдоподобно дешёвый рейс (правило README, применяется к price_rub для всех валют)')
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

CREATE TABLE IF NOT EXISTS dq.quarantine_prices (
    id                  bigserial PRIMARY KEY,
    price_id            bigint           NOT NULL,
    reason_code         varchar(32)      NOT NULL REFERENCES dq.reason_codes (code),
    loading_date        date,
    loading_city_gcid   integer,
    unloading_city_gcid integer,
    distance            double precision,
    total_weight        double precision,
    total_volume        double precision,
    price               double precision,
    currency_code       varchar(3),
    price_rub           double precision,
    price_type          smallint,
    is_fact             boolean,
    run_id              uuid             NOT NULL,
    created_at          timestamptz      NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dq_quarantine_prices_loading_date_idx
    ON dq.quarantine_prices (loading_date);
CREATE INDEX IF NOT EXISTS dq_quarantine_prices_reason_idx
    ON dq.quarantine_prices (reason_code);
