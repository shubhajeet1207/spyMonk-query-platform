# spyMonk-warehouse — unified frontend + backend image.
#
# Lives at the repo root (not inside spyMonk-warehouse/) because the build
# needs the sibling spyMonk-DB library as build context:
#
#   docker build -t spymonk-warehouse .
#
# or simply:  docker compose up --build   (see docker-compose.yml at repo root)
#
# Listens on $PORT if the host sets one (Render, Railway, Cloud Run all do);
# falls back to 7860 (Hugging Face Spaces' default) otherwise.

# Stage 1: build the React frontend
FROM node:20-slim AS frontend-build
WORKDIR /app
COPY spyMonk-warehouse/package*.json ./
RUN npm ci
COPY spyMonk-warehouse/ ./
# Empty VITE_API_URL -> frontend calls the same origin that serves it.
ARG VITE_API_URL=""
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

# Stage 2: Python backend serving the API and the built frontend
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Grafana Alloy: optionally scrapes this app's own /metrics and remote_writes
# to Grafana Cloud Prometheus (see entrypoint.sh — only runs if
# GC_PROMETHEUS_URL is set at runtime). Downloaded via Python's stdlib so no
# extra apt packages (curl/unzip) are needed just to fetch one binary.
RUN python3 -c "\
import urllib.request, zipfile; \
urllib.request.urlretrieve('https://github.com/grafana/alloy/releases/latest/download/alloy-linux-amd64.zip', '/tmp/alloy.zip'); \
zipfile.ZipFile('/tmp/alloy.zip').extractall('/tmp/alloy_extract')" \
    && mv /tmp/alloy_extract/alloy* /usr/local/bin/alloy \
    && chmod +x /usr/local/bin/alloy \
    && rm -rf /tmp/alloy.zip /tmp/alloy_extract

COPY entrypoint.sh /app/entrypoint.sh
COPY alloy-config.alloy.template /app/alloy-config.alloy.template
RUN chmod +x /app/entrypoint.sh

# Install the spyMonk-DB library from the repo copy (cache-friendly: library
# changes less often than backend code).
COPY spyMonk-DB /app/spyMonk-DB
RUN pip install --no-cache-dir /app/spyMonk-DB

COPY spyMonk-warehouse/backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY spyMonk-warehouse/backend /app/backend
COPY --from=frontend-build /app/dist /app/frontend_dist

# Run as an unprivileged user; /data holds the embedded database.
RUN useradd --create-home appuser \
    && mkdir -p /data /app/backend/logs \
    && chown -R appuser:appuser /data /app/backend /app/entrypoint.sh /app/alloy-config.alloy.template
USER appuser

EXPOSE 7860

ENV DATABASE_PATH=/data/spymonk_warehouse_db \
    ENVIRONMENT=production \
    FRONTEND_DIST_PATH=/app/frontend_dist \
    PORT=7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"7860\")}/health')"

WORKDIR /app/backend
CMD ["/app/entrypoint.sh"]
