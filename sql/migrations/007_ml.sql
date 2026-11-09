-- 007: ML-слой. Плоское представление сделки для модели.
--
-- Одна строка на сделку: у сделки может быть несколько групп кузова (витрина
-- размножает их законно), но модели нужен один детерминированный признак —
-- он предвычислен в ETL в core.price_primary_group (минимальный std_id).
--
-- Важно: здесь НЕТ и не должно быть производных целевой переменной
-- (price_km, price_group_id, агрегатов price_group_stats и т.п.).

CREATE OR REPLACE VIEW core.feature_view AS
SELECT f.price_id,
       f.loading_date,
       f.loading_city_gcid,
       f.unloading_city_gcid,
       lc.city_name  AS origin_city,
       lc.region     AS origin_region,
       lc.latitude   AS origin_lat,
       lc.longitude  AS origin_lon,
       uc.city_name  AS dest_city,
       uc.region     AS dest_region,
       uc.latitude   AS dest_lat,
       uc.longitude  AS dest_lon,
       f.distance,
       f.total_weight,
       f.total_volume,
       pg.body_group,
       f.weight_category,
       f.volume_category,
       f.price_type,
       f.is_fact,
       f.complex,
       f.outlier_mark,
       f.price_rub
FROM core.price_fact f
LEFT JOIN core.cities_dedup lc ON lc.gc_id = f.loading_city_gcid
LEFT JOIN core.cities_dedup uc ON uc.gc_id = f.unloading_city_gcid
LEFT JOIN core.price_primary_group pg ON pg.price_id = f.price_id;
