#!/bin/sh
# Renders the Alloy config (if metrics shipping is configured) and starts it
# in the background, then execs uvicorn as the container's main process.
#
# Intentionally no `set -e`: a problem in the optional metrics side must
# never prevent the app itself from starting.

CONFIG_TEMPLATE="/app/alloy-config.alloy.template"
CONFIG_OUT="/tmp/alloy-config.alloy"

if [ -n "$GC_PROMETHEUS_URL" ]; then
  sed \
    -e "s|__PORT__|${PORT:-7860}|g" \
    -e "s|__GC_PROMETHEUS_URL__|${GC_PROMETHEUS_URL}|g" \
    -e "s|__GC_PROMETHEUS_USER__|${GC_PROMETHEUS_USER}|g" \
    -e "s|__GC_PROMETHEUS_API_KEY__|${GC_PROMETHEUS_API_KEY}|g" \
    "$CONFIG_TEMPLATE" > "$CONFIG_OUT" 2>/dev/null \
    && alloy run "$CONFIG_OUT" --storage.path=/tmp/alloy-data &
  echo "[entrypoint] Started Grafana Alloy (metrics -> Grafana Cloud Prometheus)"
else
  echo "[entrypoint] GC_PROMETHEUS_URL not set; skipping Alloy (metrics stay local /metrics only)"
fi

exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-7860}"
