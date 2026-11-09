"""DAG пайплайна «Умной Логистики».

Каждый шаг — вызов того же CLI, что запускается руками:
    python -m etl.cli <команда>

Оркестратор не содержит собственной бизнес-логики: логика в пакетах etl/marts/ml,
поэтому любой шаг можно воспроизвести и без Airflow.

Инициализация источника и DDL хранилища в DAG НЕ входят: это операции развёртывания,
их выполняет сервис `airflow-init` при `docker compose up`. Иначе каждый плановый
прогон пересоздавал бы витрину (`DROP MATERIALIZED VIEW` + `CREATE`) и на время
делал её недоступной для BI.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

PROJECT_DIR = "/opt/airflow/project"
PYTHON = "python"

# Логическая дата прогона задаёт окно загрузки: [data_interval_start, data_interval_end).
# При ежедневном расписании это ровно те сутки, за которые отвечает прогон.
# Историю до начала расписания засевает docker/bootstrap.sh одним полным `load`
# при развёртывании — расписание должно грузить только свой интервал.
LOAD_COMMAND = (
    "cd " + PROJECT_DIR + " && " + PYTHON + " -m etl.cli load "
    "--start {{ data_interval_start | ds }} "
    "--end {{ data_interval_end | ds }}"
)

# Дата старта берётся из того же AS_OF_DATE, что и витрина/ML: иначе при сдвиге
# даты среза расписание осталось бы на старой и «сейчас» разъехалось бы.
START_DATE = datetime.fromisoformat(os.getenv("AS_OF_DATE", "2026-10-26"))

DEFAULT_ARGS = {
    "owner": "data-platform",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
}

with DAG(
    dag_id="smart_logistics_pipeline",
    description="ETL -> core -> mart -> ML -> batch scoring",
    start_date=START_DATE,
    schedule="0 3 * * *",
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["smart-logistics", "dwh", "ml"],
) as dag:
    load = BashOperator(
        task_id="load_core",
        bash_command=LOAD_COMMAND,
        doc_md=(
            "Окно ETL за интервал прогона: `logical_date` → "
            "[data_interval_start; data_interval_end). "
            "Атомарная перезапись окна, повторный запуск идемпотентен."
        ),
    )

    refresh_marts = BashOperator(
        task_id="refresh_marts",
        bash_command=f"cd {PROJECT_DIR} && {PYTHON} -m etl.cli marts",
        doc_md="Пересчёт витрин: REFRESH MATERIALIZED VIEW CONCURRENTLY.",
    )

    ml_train = BashOperator(
        task_id="ml_train",
        bash_command=f"cd {PROJECT_DIR} && {PYTHON} -m etl.cli ml-train",
        doc_md="Обучение CatBoost и бейзлайна, метрики на отложенном периоде.",
    )

    ml_predict = BashOperator(
        task_id="ml_predict",
        bash_command=f"cd {PROJECT_DIR} && {PYTHON} -m etl.cli ml-predict",
        doc_md="Пакетный скоринг теста и плановых заявок, запись в mart.ml_price_predictions.",
    )

    verify = BashOperator(
        task_id="verify",
        bash_command=f"cd {PROJECT_DIR} && {PYTHON} -m etl.cli verify",
        doc_md="Проверка, что core, витрина и прогнозы не пусты.",
    )

    load >> refresh_marts >> ml_train >> ml_predict >> verify
