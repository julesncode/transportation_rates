# Образ пайплайна: Python 3.11 + клиент psql (нужен для инициализации источника,
# так как sql/synthetic.sql использует psql-переменную :n_prices).
#
# Два target:
#   base — рантайм (ETL, витрины, ML);
#   test — base + pytest, используется сервисом tests (profile tools).
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Слой зависимостей отделён от кода: правки кода не переустанавливают пакеты.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

CMD ["python", "-m", "etl.cli", "check"]

FROM base AS test

COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

CMD ["pytest"]
