-- База метаданных Airflow создаётся при первой инициализации тома.
-- На уже существующем томе её создаёт шаг `etl.cli ensure-airflow-db`
-- (см. сервис airflow-init).
CREATE DATABASE airflow;
