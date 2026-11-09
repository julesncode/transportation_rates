# Образ Airflow с кодом проекта внутри.
# Код не монтируется с хоста: DAG и модули лежат в /opt/airflow/project,
# а DAGS_FOLDER указывает на /opt/airflow/project/dags.
#
# Зависимости проекта ставятся отдельным списком (docker/airflow-requirements.txt):
# полный requirements.txt обновил бы pandas/numpy в базовом образе Airflow,
# что может сломать провайдеры.
FROM apache/airflow:2.10.5-python3.11

# psql нужен шагу init_source: sql/synthetic.sql использует psql-переменную :n_prices.
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*

USER airflow

COPY docker/airflow-requirements.txt /tmp/airflow-requirements.txt
RUN pip install --no-cache-dir -r /tmp/airflow-requirements.txt

COPY --chown=airflow:root . /opt/airflow/project

ENV PYTHONPATH=/opt/airflow/project \
    AIRFLOW__CORE__DAGS_FOLDER=/opt/airflow/project/dags

WORKDIR /opt/airflow/project
