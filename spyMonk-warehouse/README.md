# spyMonk-warehouse

A Snowflake-inspired data warehouse built on [spyMonk-DB](../spyMonk-DB). Upload CSV/JSON/XLSX files, query them with SQL (including JOINs), and get warehouse-grade performance features: columnar micro-partitions, zone-map partition pruning, and a versioned result cache.

**Stack:** FastAPI backend · React 19 + Vite frontend · spyMonk-DB as the storage engine · SQLite as the in-memory SQL executor.

## Architecture

```
Upload (CSV/JSON/XLSX)
   └─> pandas DataFrame
        └─> columnar micro-partitions (~5000 rows each, msgpack)
             part:<table>:<version>:<idx>  in spyMonk-DB
             + zone maps (min/max/null_count per column) in table meta

Query (SQL)
   ├─> result cache lookup  (key = normalized SQL + version of every table)
   ├─> extract ALL referenced tables (JOINs, subqueries, CTEs)
   ├─> zone-map pruning: skip partitions that provably cannot match
   └─> load surviving partitions -> in-memory SQLite -> execute -> cache
```

Three properties make this scale:

1. **Pruning is only an optimization.** A partition is skipped only when its zone map *proves* no row can match; any uncertainty loads the partition. SQLite always executes the full query over whatever was loaded, so results are always correct.
2. **Cache invalidation is implicit.** Every upload/delete bumps a never-reused table version (`tablever:<table>`), and cache keys embed the versions of every table a query touches. Stale entries simply stop being addressed.
3. **Tables written by older builds still work.** The legacy row-per-key layout (`data:<table>:<idx>`) remains queryable through a read fallback; new uploads always write the partition format.

## Quick start

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e ../../spyMonk-DB        # the storage engine, from the sibling repo

cp .env.example .env                   # then edit: set ALLOWED_API_KEYS, AI_API_KEY (optional)
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
npm install
cp .env.example .env.local             # points at http://localhost:8000 with the dev key
npm run dev                            # http://localhost:5173
```

### Tests

```bash
cd backend && python -m pytest tests -v
```

The suite covers storage round-trips, pruning correctness (including a property test that pruning never drops matching rows), cache invalidation, JOIN queries end-to-end, auth, and the legacy-layout fallback — no running database required (a fake KV stands in for spyMonk-DB).

## API

All endpoints except `/health` require an `X-API-Key` header matching one of `ALLOWED_API_KEYS`. In development mode with no keys configured, auth is bypassed.

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Liveness + mode + result-cache stats |
| `/upload` | POST | Upload CSV/JSON/XLSX; stores as versioned micro-partitions |
| `/query` | POST | Execute a SELECT (JOINs supported). Body: `{query, query_id?, use_cache?}` |
| `/query/cancel/{id}` | POST | Cooperatively cancel a running query |
| `/tables` | GET | List tables with schema and row counts |
| `/tables/{name}` | DELETE | Drop a table (bumps version, invalidating cached results) |
| `/ai/assist` | POST | AI SQL helper: optimize / generate-from-English / fix (needs `AI_API_KEY`) |

`/query` responses include observability fields: `cache_hit`, `partitions_scanned`, `partitions_total`, `tables_used`.

Only SELECT statements are accepted; queries are validated with comment-stripping and word-boundary keyword checks (see `auth.py`), and table names are allow-listed against `[a-zA-Z][a-zA-Z0-9_]*`.

## Configuration

Set in `backend/.env` (see `backend/.env.example` for the full list):

| Variable | Default | Purpose |
|---|---|---|
| `ALLOWED_API_KEYS` | *(empty)* | Comma-separated client keys; empty + development = open access |
| `USE_DISTRIBUTED_MODE` | `false` | `true` connects to a spyMonk-DB cluster instead of embedding one |
| `SPYMONK_DB_NODES` | `localhost:5000` | Cluster node addresses (distributed mode) |
| `SPYMONK_DB_AUTH_TOKEN` | *(empty)* | Bearer token for cluster RPCs |
| `DATABASE_PATH` | `data/spymonk_warehouse_db` | Embedded database directory |
| `PARTITION_ROWS` | `5000` | Rows per micro-partition |
| `RESULT_CACHE_MAX_ENTRIES` / `RESULT_CACHE_TTL_SECONDS` | `256` / `1800` | Result cache sizing |
| `PRUNING_ENABLED` | `true` | Toggle zone-map pruning (correctness never depends on it) |
| `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL` | *(empty)*, OpenAI, `gpt-4o-mini` | AI assistant (any OpenAI-compatible endpoint) |

## Docker

The image bundles the built frontend and the backend in one process, and needs the sibling `spyMonk-DB` directory — so build from the **repo root** (the parent of this directory), where the `Dockerfile` lives:

```bash
docker build -t spymonk-warehouse .
# or, simpler:
docker compose up --build          # uses docker-compose.yml at the repo root
```

## Observability

- Requests are logged as structured JSON (with request IDs and durations) to stdout and `backend/logs/app.log`.
- The repo-root monitoring stack (`docker compose -f ../docker-compose.monitoring.yml up -d`) ships that log file to Loki via Promtail and provisions Grafana at http://localhost:3000 (admin/admin).

### Production (deployed instance)

The Render deployment ships logs and metrics to Grafana Cloud (see `entrypoint.sh`, `loki_handler.py`, and `alloy-config.alloy.template` at the repo root):

- **Dashboard**: https://spymonk.grafana.net/d/spymonk-production/spymonk-warehouse-production
- **Logs (Explore)**: https://spymonk.grafana.net/explore?schemaVersion=1&panes=%7B%22cxh%22:%7B%22datasource%22:%22grafanacloud-logs%22,%22queries%22:%5B%7B%22refId%22:%22A%22,%22expr%22:%22%7Bapp%3D%5C%22spymonk-warehouse%5C%22%7D%20%7C%3D%20%60%60%22,%22queryType%22:%22range%22,%22datasource%22:%7B%22type%22:%22loki%22,%22uid%22:%22grafanacloud-logs%22%7D,%22editorMode%22:%22builder%22,%22direction%22:%22backward%22%7D%5D,%22range%22:%7B%22from%22:%22now-1h%22,%22to%22:%22now%22%7D,%22panelsState%22:%7B%22logs%22:%7B%22sortOrder%22:%22Descending%22%7D%7D,%22compact%22:false%7D%7D

## Project layout

```
backend/
  main.py           FastAPI app: endpoints, lifespan, middleware
  storage.py        micro-partition storage + zone maps + versioning
  query_engine.py   table/predicate extraction + partition selection
  result_cache.py   LRU+TTL cache keyed on SQL + table versions
  auth.py           API-key auth + SQL/table-name/file validation
  config.py         pydantic-settings configuration
  tests/            unit + API integration tests (fake KV, no DB needed)
src/                React frontend (Monaco SQL editor, uploader, results table)
testing-script/     generator for large sample CSVs
```
