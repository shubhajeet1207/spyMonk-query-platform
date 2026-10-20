# Install, run, and deploy spyMonk Query Platform

The repository contains two connected components:

- **`spyMonk-DB`** — the Python storage engine and gRPC server.
- **`spyMonk-warehouse`** — the FastAPI query service and React workspace built on top of the database library.

The fastest path is Docker Compose. Local development runs the warehouse against an embedded spyMonk-DB instance; a distributed database cluster is an opt-in deployment mode.

## Requirements

Choose the path that fits your use case:

| Path | Requirements |
| --- | --- |
| Docker | Docker Engine with Docker Compose v2 |
| Local development | Python 3.11+, Node.js 20+, and npm |
| Optional monitoring | Docker Engine with sufficient permissions for cAdvisor and node-exporter |

## Quick start with Docker

From the repository root:

```bash
docker compose up --build
```

Open [http://localhost:8000](http://localhost:8000). This starts the combined warehouse image, serves its React interface and FastAPI API from the same origin, and persists embedded database data in the `warehouse-data` Docker volume.

For an external API client, configure `ALLOWED_API_KEYS` before starting Compose, then send the key in `X-API-Key`. The same-origin web interface does not need a browser-visible secret.

```bash
# Bash example; keep this value out of version control.
export ALLOWED_API_KEYS="$(openssl rand -hex 32)"
docker compose up --build
```

The API health endpoint is available at `http://localhost:8000/health`. Stop the stack with `docker compose down`; add `-v` only when you deliberately want to remove the persisted warehouse volume.

## Local development

### 1. Start the FastAPI backend

```bash
cd spyMonk-warehouse/backend
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# PowerShell:   .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip install -e ../../spyMonk-DB
```

Copy the environment template and configure local settings:

```bash
# macOS/Linux
cp .env.example .env

# PowerShell
Copy-Item .env.example .env
```

For normal local development, keep `ENVIRONMENT=development`, `USE_DISTRIBUTED_MODE=false`, and `CORS_ORIGINS=http://localhost:5173`. The example file contains development API keys; replace them before exposing an API. Start the backend:

```bash
uvicorn main:app --reload --port 8000
```

### 2. Start the React workspace

In a second terminal:

```bash
cd spyMonk-warehouse
npm ci
cp .env.example .env.local # PowerShell: Copy-Item .env.example .env.local
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). `VITE_API_URL` defaults to `http://localhost:8000` for development; set it only when the API runs elsewhere. If API-key authentication is enabled, `VITE_API_KEY` must match one of the backend's `ALLOWED_API_KEYS` values. Do not put an AI-provider key in a `VITE_*` variable.

## Test and build

Run the database and warehouse suites from their respective directories:

```bash
cd spyMonk-DB
python -m pip install -e .[dev]
python -m pytest tests -v

cd ../spyMonk-warehouse/backend
python -m pytest tests -v

cd ..
npm run lint
npm run build
```

The warehouse backend tests use a fake key-value layer and do not require a running database service. The built frontend is served by the FastAPI container in the Docker image.

## Configuration

The full backend reference is [`spyMonk-warehouse/backend/.env.example`](../../spyMonk-warehouse/backend/.env.example). The most important settings are:

| Variable | Purpose |
| --- | --- |
| `ENVIRONMENT` | `development` enables API docs; set `production` for a deployed service. |
| `ALLOWED_API_KEYS` | Comma-separated keys for direct API clients. |
| `CORS_ORIGINS` | Comma-separated browser origins allowed to call the API. |
| `USE_DISTRIBUTED_MODE` | `false` embeds the database; `true` connects to database nodes. |
| `SPYMONK_DB_NODES` / `SPYMONK_DB_AUTH_TOKEN` | Cluster addresses and shared token when using distributed mode. |
| `DATABASE_PATH` | Durable path for embedded-mode data. |
| `UPLOAD_MAX_SIZE_MB` | Per-upload size limit; the default is 100 MB. |
| `PARTITION_ROWS` | Approximate row count per columnar micro-partition; the default is 5,000. |
| `RESULT_CACHE_MAX_ENTRIES` / `RESULT_CACHE_TTL_SECONDS` | Query result-cache capacity and lifetime. |
| `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL` | Optional OpenAI-compatible SQL-assistant provider. Keep the key backend-only. |

## Monitoring

To run the local Grafana, Prometheus, Loki, and Promtail stack:

```bash
docker compose -f docker-compose.monitoring.yml up -d
```

Grafana is available at [http://localhost:3000](http://localhost:3000) with the local default credentials `admin` / `admin`. This monitoring compose file also includes host- and container-level collectors; review its permissions before running it on a shared machine.

The warehouse exposes Prometheus-format metrics at `/metrics`. In hosted deployments, the image can start Grafana Alloy when the `GC_PROMETHEUS_*` variables are configured, and it can ship logs to Loki when `LOKI_URL` and related credentials are set.

## Production notes

- Use the root `Dockerfile` or Compose build so the image includes both the warehouse and its sibling `spyMonk-DB` library.
- Provide durable storage for `DATABASE_PATH` in embedded mode. Container-local data is lost on replacement without a mounted volume.
- Set `ENVIRONMENT=production`, restrict `CORS_ORIGINS` to real HTTPS origins, generate strong API keys, and configure `ALLOWED_API_KEYS` for direct clients.
- When deploying a separate database cluster, set `USE_DISTRIBUTED_MODE=true`, configure every node address, and use both database bearer-token authentication and TLS for the gRPC boundary.
- Treat the AI assistant as an optional external service: it receives the request context needed to propose a safe `SELECT` query.
- Plan backups and test restore procedures before accepting important data.

For deployment topology options and package-distribution notes, see [the deployment roadmap](../../DEPLOYMENT_ROADMAP.md) and [the packaging guide](../../PACKAGING_GUIDE.md). Those documents include historical planning material; this guide describes the repository's current local and Docker workflow.
