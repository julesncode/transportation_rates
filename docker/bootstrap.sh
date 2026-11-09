#!/usr/bin/env bash
# Однократный прогон DAG при `docker compose up`.
#
# DAG намеренно остаётся на паузе: снятие паузы заставляет планировщик сразу
# создать ещё один прогон за последний интервал, и он пошёл бы параллельно с
# бэкфиллом. Бэкфилл работает и на DAG в паузе, поэтому демо-прогон ровно один.
# Включить ежедневное расписание: `airflow dags unpause smart_logistics_pipeline`.
#
# Бэкфилл блокирующий: сервис завершается только после успеха или падения пайплайна.
set -euo pipefail

DAG_ID="smart_logistics_pipeline"
LOGICAL_DATE="${AS_OF_DATE:-2026-10-26}"
# Бэкфилл принимает диапазон дат, внутри которого должен попасть тик расписания
# (у DAG это 03:00). Диапазон в одни сутки от даты снимка как раз его содержит.
END_DATE="$(date -d "${LOGICAL_DATE} +1 day" +%F)"

echo "[bootstrap] ожидание регистрации DAG ${DAG_ID}"
for _ in $(seq 1 60); do
    if airflow dags list 2>/dev/null | grep -q "${DAG_ID}"; then
        echo "[bootstrap] DAG найден"
        break
    fi
    sleep 5
done

# Засев истории: расписание грузит только свой интервал (logical_date), поэтому
# полную историю нужно загрузить один раз — и только если core ещё пуст.
# На повторном подъёме засев пропускается: свежие сутки загрузит сам DAG.
CORE_ROWS="$(
    psql -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
         -tAc "SELECT count(*) FROM core.price_fact" 2>/dev/null || echo unknown
)"
if [ "${CORE_ROWS}" = "0" ]; then
    echo "[bootstrap] засев истории (core пуст — полная загрузка)"
    python -m etl.cli load
elif [ "${CORE_ROWS}" = "unknown" ]; then
    echo "[bootstrap] не удалось проверить core — засев пропущен, историю загрузит DAG"
else
    echo "[bootstrap] засев не нужен: в core ${CORE_ROWS} строк"
fi

echo "[bootstrap] запуск прогона за ${LOGICAL_DATE}"
# -y обязателен: --reset-dagruns иначе спрашивает подтверждение, а stdin в контейнере закрыт.
airflow dags backfill "${DAG_ID}" -s "${LOGICAL_DATE}" -e "${END_DATE}" --reset-dagruns -y

echo "[bootstrap] прогон завершён успешно"
