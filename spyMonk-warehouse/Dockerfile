# spyMonk-warehouse — unified frontend + backend image.
#
# IMPORTANT: build from the REPO ROOT (the parent of spyMonk-warehouse/),
# because the image needs the sibling spyMonk-DB library:
#
#   docker build -f spyMonk-warehouse/Dockerfile -t spymonk-warehouse .
#
# or simply:  docker compose up --build   (see docker-compose.yml at repo root)

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
    && chown -R appuser:appuser /data /app/backend
USER appuser

EXPOSE 8000

ENV DATABASE_PATH=/data/spymonk_warehouse_db \
    ENVIRONMENT=production \
    FRONTEND_DIST_PATH=/app/frontend_dist

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

WORKDIR /app/backend
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
