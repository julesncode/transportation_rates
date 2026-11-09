-- 006: витрина «коридор рыночных ставок».
--
-- Зерно: месяц x город отправления x город назначения x группа кузова x весовая категория.
-- Месяц берётся из price_dates.price_date (а не из loading_date): долгосрочная
-- заявка законно присутствует в нескольких месяцах.
--
-- Уникальный индекс нужен и для REFRESH ... CONCURRENTLY, и для BI-запросов.

DROP MATERIALIZED VIEW IF EXISTS mart.mart_market_corridor;

CREATE MATERIALIZED VIEW mart.mart_market_corridor AS
WITH params AS (
    SELECT as_of, freshness_window_days
    FROM mart.pipeline_params
    WHERE id
), facts AS (
    -- Одна строка на (сделка, месяц, группа кузова). У сделки может быть
    -- несколько price_dates внутри одного месяца — их нельзя отдавать в
    -- перцентили дважды, иначе одна и та же цена взвешивается два раза.
    -- Группировка схлопывает такие даты, сохраняя максимальную для last_price_date;
    -- разные группы кузова дают разные ячейки, а не дубли внутри одной.
    SELECT date_trunc('month', d.price_date)::date AS month,
           f.loading_city_gcid,
           f.unloading_city_gcid,
           g.body_group,
           f.weight_category,
           f.price_id,
           f.price_rub,
           f.distance,
           f.price_type,
           f.organization_id,
           max(d.price_date) AS last_price_date
    FROM core.price_fact f
    JOIN core.price_body_group g ON g.price_id = f.price_id
    JOIN core.price_date d       ON d.price_id = f.price_id
    CROSS JOIN params p
    WHERE f.is_fact
      AND NOT f.complex
      AND f.price_rub > 0
      AND f.distance > 0
      AND NOT f.outlier_mark
      -- Плановые месяцы из будущего не должны попадать в историю рынка.
      -- Фильтр построчный: октябрьская ячейка остаётся, но только по датам <= as_of.
      AND d.price_date <= p.as_of
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
), agg AS (
    SELECT month,
           loading_city_gcid,
           unloading_city_gcid,
           body_group,
           weight_category,
           count(DISTINCT price_id)                                        AS trips_count,
           count(DISTINCT organization_id) FILTER (WHERE price_type IN (3, 9)) AS carriers_count,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY price_rub)          AS p25_trip,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY price_rub)          AS median_trip,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY price_rub)          AS p75_trip,
           avg(price_rub)                                                   AS avg_trip,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY price_rub / NULLIF(distance, 0)) AS p25_km,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY price_rub / NULLIF(distance, 0)) AS median_km,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY price_rub / NULLIF(distance, 0)) AS p75_km,
           stddev_samp(price_rub)                                           AS std_trip,
           -- Фактическая свежесть данных в ячейке: справочная величина для BI.
           -- Флаг надёжности считается по месяцу (см. reliability_level ниже).
           max(last_price_date)                                             AS last_price_date
    FROM facts
    GROUP BY 1, 2, 3, 4, 5
)
SELECT a.month,
       a.loading_city_gcid,
       a.unloading_city_gcid,
       lc.city_name AS loading_city,
       uc.city_name AS unloading_city,
       a.body_group,
       a.weight_category,
       a.trips_count,
       a.carriers_count,
       a.p25_trip,
       a.median_trip,
       a.p75_trip,
       a.avg_trip,
       a.p25_km,
       a.median_km,
       a.p75_km,
       a.last_price_date,
       -- Коэффициент вариации: при < 2 наблюдениях или нулевом среднем — NULL.
       CASE WHEN a.trips_count < 2 OR a.avg_trip = 0 THEN NULL
            ELSE a.std_trip / a.avg_trip END AS cv_rate,
       -- MoM/YoY — только к календарному соседнему месяцу. Если ячейки нет,
       -- результат NULL: подставлять «последний доступный» месяц запрещено.
       CASE WHEN prev.median_trip IS NULL OR prev.median_trip = 0 THEN NULL
            ELSE 100.0 * (a.median_trip - prev.median_trip) / prev.median_trip END AS mom_pct,
       CASE WHEN yoy.median_trip IS NULL OR yoy.median_trip = 0 THEN NULL
            ELSE 100.0 * (a.median_trip - yoy.median_trip) / yoy.median_trip END AS yoy_pct,
       -- Надёжность: объём + конкуренция + свежесть месяца.
       -- Свежесть: месяц пересекается с окном [as_of - N дней; as_of]
       -- (решение Q4; месяц — интервал, а не точка).
       CASE WHEN a.trips_count >= 30
                 AND a.carriers_count >= 5
                 AND (a.month + INTERVAL '1 month' - INTERVAL '1 day')::date
                     >= p.as_of - p.freshness_window_days
            THEN 'HIGH'
            WHEN a.trips_count >= 10 THEN 'MEDIUM'
            ELSE 'LOW_DATA'
       END AS reliability_level,
       p.as_of
FROM agg a
CROSS JOIN params p
LEFT JOIN core.cities_dedup lc ON lc.gc_id = a.loading_city_gcid
LEFT JOIN core.cities_dedup uc ON uc.gc_id = a.unloading_city_gcid
LEFT JOIN agg prev
       ON prev.month = (a.month - INTERVAL '1 month')::date
      AND prev.loading_city_gcid = a.loading_city_gcid
      AND prev.unloading_city_gcid = a.unloading_city_gcid
      AND prev.body_group = a.body_group
      AND prev.weight_category = a.weight_category
LEFT JOIN agg yoy
       ON yoy.month = (a.month - INTERVAL '12 months')::date
      AND yoy.loading_city_gcid = a.loading_city_gcid
      AND yoy.unloading_city_gcid = a.unloading_city_gcid
      AND yoy.body_group = a.body_group
      AND yoy.weight_category = a.weight_category;

-- Уникальный индекс: обязателен для REFRESH ... CONCURRENTLY.
CREATE UNIQUE INDEX IF NOT EXISTS mart_market_corridor_grain_idx
    ON mart.mart_market_corridor (month, loading_city_gcid, unloading_city_gcid, body_group, weight_category);

-- Типовые срезы BI: карточка маршрута и сводка по месяцам.
CREATE INDEX IF NOT EXISTS mart_market_corridor_route_idx
    ON mart.mart_market_corridor (loading_city_gcid, unloading_city_gcid, body_group, weight_category, month);
CREATE INDEX IF NOT EXISTS mart_market_corridor_month_idx
    ON mart.mart_market_corridor (month);
