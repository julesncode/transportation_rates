-- 001: схемы хранилища. Источник живёт в public (deals_bigdata), наш DWH — в отдельных схемах.
CREATE SCHEMA IF NOT EXISTS raw;   -- побайтовые копии источника (для воспроизводимости)
CREATE SCHEMA IF NOT EXISTS core;  -- очищенные и нормализованные данные
CREATE SCHEMA IF NOT EXISTS dq;    -- карантин отбракованных строк
CREATE SCHEMA IF NOT EXISTS mart;  -- аналитические витрины и прогнозы
