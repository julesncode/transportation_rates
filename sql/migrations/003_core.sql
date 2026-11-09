-- 003: слой core — очищенные сделки, группы кузова, помесячная развёртка.
-- Справочники представлены view поверх raw: они не требуют отдельной загрузки
-- и всегда соответствуют последней копии источника.

-- --------------------------------------------------------------------------- #
-- Доменные функции: единая точка правды для категорий и групп кузова.
-- Используются и в ETL, и в тестах, и в витринах — расхождений быть не может.
-- --------------------------------------------------------------------------- #
CREATE OR REPLACE FUNCTION core.weight_category(p_weight double precision)
RETURNS varchar(4) LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN p_weight IS NULL THEN 'OTH'
        WHEN round(p_weight::numeric, 1) >= 30  THEN 'G20T'
        WHEN round(p_weight::numeric, 1) >= 10  THEN 'FTL'
        WHEN round(p_weight::numeric, 1) >= 5   THEN '10T'
        WHEN round(p_weight::numeric, 1) >= 2.5 THEN '5T'
        WHEN round(p_weight::numeric, 1) >= 0.5 THEN 'L5T'
        ELSE 'OTH'
    END
$$;

CREATE OR REPLACE FUNCTION core.volume_category(p_volume double precision)
RETURNS varchar(4) LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN p_volume IS NOT NULL AND p_volume > 0 AND p_volume <= 120 THEN 'FTL'
        ELSE 'OTH'
    END
$$;

-- Стандартизированные группы кузова (Вопрос 2). NULL std_id -> 'Не указан'.
CREATE OR REPLACE FUNCTION core.body_group_name(p_std_id integer)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN p_std_id IS NULL THEN 'Не указан'
        WHEN p_std_id = 56    THEN 'Тент'
        WHEN p_std_id = 4     THEN 'Рефрижератор'
        WHEN p_std_id = 2     THEN 'Изотерм'
        WHEN p_std_id = 15    THEN 'Борт'
        ELSE 'Прочие'
    END
$$;

-- --------------------------------------------------------------------------- #
-- Справочники
-- --------------------------------------------------------------------------- #
-- Дедупликация городов: gc_id не уникален, берём строку с минимальным id.
CREATE OR REPLACE VIEW core.cities_dedup AS
SELECT DISTINCT ON (gc_id)
       gc_id,
       id                                                        AS city_id,
       name,
       full_name_local,
       btrim(split_part(full_name_local, ',', 1))                AS city_name,
       NULLIF(btrim(split_part(full_name_local, ',', 2)), '')    AS region,
       latitude,
       longitude
FROM raw.cities
WHERE gc_id IS NOT NULL
ORDER BY gc_id, id;

CREATE OR REPLACE VIEW core.body_types AS
SELECT id,
       name,
       "id_from_1C",
       std_id,
       core.body_group_name(std_id) AS body_group
FROM raw.body_types;

-- --------------------------------------------------------------------------- #
-- Факты
-- --------------------------------------------------------------------------- #
CREATE TABLE IF NOT EXISTS core.price_fact (
    price_id            bigint PRIMARY KEY,
    loading_date        date             NOT NULL,
    loading_city_gcid   integer          NOT NULL,
    unloading_city_gcid integer          NOT NULL,
    distance            double precision NOT NULL,
    total_weight        double precision NOT NULL,
    total_volume        double precision NOT NULL,
    body_type           integer,
    price               double precision NOT NULL,
    currency_code       varchar(3)       NOT NULL,
    exchange_rate       double precision NOT NULL,
    price_rub           double precision NOT NULL,
    price_km            double precision,
    weight_category     varchar(4)       NOT NULL,
    volume_category     varchar(4)       NOT NULL,
    price_type          smallint         NOT NULL,
    is_fact             boolean          NOT NULL,
    complex             boolean          NOT NULL,
    organization_id     integer,
    outlier_mark        boolean          NOT NULL,
    nearest_rate_date   date,
    end_loading_date    date,
    created_at          timestamp(0),
    run_id              uuid             NOT NULL,   -- прогон, загрузивший строку
    loaded_at           timestamptz      NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS core_price_fact_loading_date_idx
    ON core.price_fact (loading_date);
CREATE INDEX IF NOT EXISTS core_price_fact_route_idx
    ON core.price_fact (loading_city_gcid, unloading_city_gcid);
CREATE INDEX IF NOT EXISTS core_price_fact_fact_idx
    ON core.price_fact (loading_date) WHERE is_fact;

-- Одна сделка может попасть в несколько стандартных групп кузова (например,
-- тент + борт), но внутри одной группы дубля быть не может.
CREATE TABLE IF NOT EXISTS core.price_body_group (
    price_id             bigint      NOT NULL REFERENCES core.price_fact (price_id) ON DELETE CASCADE,
    body_group           varchar(16) NOT NULL,
    std_id               integer,
    source_body_type_id  integer,
    PRIMARY KEY (price_id, body_group)
);
CREATE INDEX IF NOT EXISTS core_price_body_group_group_idx
    ON core.price_body_group (body_group);

-- Первичная группа кузова для ML: ровно одна строка на сделку.
-- Предвычисляется в ETL, чтобы `core.feature_view` не пересобирал DISTINCT ON
-- по всей таблице групп (335 тыс. строк) на каждый запрос обучения и скоринга.
CREATE TABLE IF NOT EXISTS core.price_primary_group (
    price_id   bigint      PRIMARY KEY REFERENCES core.price_fact (price_id) ON DELETE CASCADE,
    body_group varchar(16) NOT NULL,
    std_id     integer
);

-- Помесячная развёртка заявки: одна цена может присутствовать в нескольких месяцах.
CREATE TABLE IF NOT EXISTS core.price_date (
    price_id   bigint NOT NULL REFERENCES core.price_fact (price_id) ON DELETE CASCADE,
    price_date date   NOT NULL,
    PRIMARY KEY (price_id, price_date)
);
CREATE INDEX IF NOT EXISTS core_price_date_price_date_idx
    ON core.price_date (price_date);
