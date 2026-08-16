from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import pandas as pd
import httpx
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import json
import uuid
import sqlite3
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Optional, Literal
import io
import os
import logging
import threading
import math
import time

# Import local modules
from config import settings
from auth import (
    verify_api_key,
    is_safe_table_name,
    sanitize_sql_query,
    validate_file_size,
    validate_file_extension
)
from storage import TableStorage
from query_engine import extract_tables, extract_predicates, select_partitions
from result_cache import ResultCache, normalize_sql

LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", "logs/app.log")
os.makedirs(os.path.dirname(LOG_FILE_PATH) or ".", exist_ok=True)

# Configure logging to stdout and a local file that Promtail can scrape.
_log_handlers = [logging.StreamHandler(), logging.FileHandler(LOG_FILE_PATH)]

# Optional: ship logs straight to a hosted Loki (e.g. Grafana Cloud) for
# deployments where no local Promtail can reach the container's filesystem.
LOKI_URL = os.getenv("LOKI_URL", "")
if LOKI_URL:
    from loki_handler import LokiHandler
    _log_handlers.append(LokiHandler(
        url=LOKI_URL,
        username=os.getenv("LOKI_USERNAME", ""),
        password=os.getenv("LOKI_API_KEY", ""),
        labels={
            "job": "spymonk-warehouse-backend",
            "app": "spymonk-warehouse",
            "service": "spymonk-warehouse-backend",
            "component": "backend",
            "environment": settings.ENVIRONMENT,
        },
    ))

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)

# --- Prometheus metrics -----------------------------------------------------
# HTTP-level metrics, recorded by the log_requests middleware. Labelled by the
# route *template* (e.g. /tables/{table_name}) rather than the concrete path to
# keep label cardinality bounded.
HTTP_REQUESTS = Counter(
    "spymonk_warehouse_http_requests_total",
    "Total HTTP requests handled by the warehouse API",
    ["method", "path", "status"],
)
HTTP_REQUEST_LATENCY = Histogram(
    "spymonk_warehouse_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# spyMonk-DB engine metrics (spymonk_* series). Instantiating the collector
# registers its series in the default Prometheus registry, so /metrics exposes
# them; the /query and /upload handlers feed it real read/write timings. Guarded
# so the API still serves (and exposes HTTP metrics) if the library is absent.
try:
    from spymonk_enterprise.observability.metrics.prometheus_exporter import (
        MetricsCollector,
    )
    db_metrics = MetricsCollector(node_id=os.getenv("NODE_ID", "warehouse-1"))
except Exception as exc:  # pragma: no cover - defensive
    logger.warning(f"spyMonk-DB metrics unavailable, exposing HTTP metrics only: {exc}")
    db_metrics = None

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

# Snowflake-style result cache: keys embed table versions, so uploads/deletes
# invalidate implicitly (see result_cache.py).
result_cache = ResultCache(settings.RESULT_CACHE_MAX_ENTRIES,
                           settings.RESULT_CACHE_TTL_SECONDS)

# spyMonk-DB client (embedded or distributed), initialized by the lifespan hook.
client = None
active_queries = set()
cancelled_queries = set()
query_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the spyMonk-DB client before serving; stop it on shutdown."""
    global client

    try:
        if settings.USE_DISTRIBUTED_MODE:
            logger.info("Starting spyMonk-DB in DISTRIBUTED mode")
            logger.info(f"Connecting to nodes: {settings.SPYMONK_DB_NODES}")

            from spymonk_enterprise.client import DistributedClient
            client = DistributedClient(
                nodes=settings.SPYMONK_DB_NODES,
                auth_token=settings.SPYMONK_DB_AUTH_TOKEN
            )
            logger.info("Connected to distributed spyMonk-DB cluster")
        else:
            logger.info(f"Starting spyMonk-DB in EMBEDDED mode at {settings.DATABASE_PATH}")

            from spymonk_enterprise.client import SpyMonkClient
            client = SpyMonkClient(settings.DATABASE_PATH)
            client.start()
            logger.info("Started embedded spyMonk-DB instance")
    except Exception as e:
        logger.error(f"Failed to initialize database client: {e}")
        raise

    yield

    try:
        if not settings.USE_DISTRIBUTED_MODE and client:
            logger.info("Stopping embedded spyMonk-DB")
            client.stop()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


def get_client():
    """Return the live DB client or fail with 503 instead of an AttributeError."""
    if client is None:
        raise HTTPException(status_code=503, detail="Database is not available")
    return client


app = FastAPI(
    title="spyMonk-warehouse API",
    version="1.0.0",
    docs_url="/docs" if settings.ENVIRONMENT == "development" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT == "development" else None,
    lifespan=lifespan,
)

# Add rate limit exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Prometheus scrape endpoint. No auth: scrape only over a trusted network
# (in Docker it is reached from the Prometheus container, not the public API).
# The log_requests middleware skips this path so scrapes don't spam the logs.
@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

# Configure CORS with specific origins. DELETE is required for the UI's
# drop-table action — browsers preflight it with OPTIONS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
    max_age=3600,
)

class QueryCancelled(Exception):
    """Raised when a running query has been cancelled by the user."""


# Per-table query history ("last N queries"), stored at queryhist:<table>.
# Table names never contain ':' so the prefix can't collide with table:/part:/
# tablever: keys, and list_tables' scan of "table:" never picks these up.
QUERY_HISTORY_LIMIT = 5
query_history_lock = threading.Lock()


def _history_key(table: str) -> bytes:
    return f"queryhist:{table}".encode()


def record_query_history(db, tables: List[str], query: str,
                         row_count: int, cache_hit: bool) -> None:
    """Prepend this query to each touched table's history. Never raises."""
    entry = {
        "query": query,
        "at": pd.Timestamp.now().isoformat(),
        "row_count": row_count,
        "cache_hit": cache_hit,
    }
    try:
        with query_history_lock:
            for t in tables:
                raw = db.get(_history_key(t))
                try:
                    history = json.loads(raw.decode()) if raw else []
                except (ValueError, AttributeError):
                    history = []
                history.insert(0, entry)
                db.put(_history_key(t),
                       json.dumps(history[:QUERY_HISTORY_LIMIT]).encode())
    except Exception as exc:
        logger.warning(f"Failed to record query history: {exc}")


def register_query(query_id: str) -> None:
    with query_lock:
        active_queries.add(query_id)
        cancelled_queries.discard(query_id)


def complete_query(query_id: str) -> None:
    with query_lock:
        active_queries.discard(query_id)
        cancelled_queries.discard(query_id)


def is_query_cancelled(query_id: str) -> bool:
    with query_lock:
        return query_id in cancelled_queries


def raise_if_cancelled(query_id: str) -> None:
    if is_query_cancelled(query_id):
        raise QueryCancelled()


def make_json_safe(value):
    """Convert Pandas/Numpy missing and non-finite values into valid JSON values."""
    if isinstance(value, dict):
        return {key: make_json_safe(item) for key, item in value.items()}

    if isinstance(value, list):
        return [make_json_safe(item) for item in value]

    if value is None or isinstance(value, (str, bool, int)):
        return value

    if isinstance(value, float):
        return value if math.isfinite(value) else None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if hasattr(value, "item"):
        try:
            return make_json_safe(value.item())
        except (TypeError, ValueError):
            pass

    return str(value)


def _route_label(request: Request) -> str:
    """Route template (e.g. /tables/{table_name}) for low-cardinality labels."""
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Don't log or self-count Prometheus scrapes (they hit /metrics every ~15s).
    if request.url.path == "/metrics" or request.url.path.startswith("/metrics/"):
        return await call_next(request)

    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())

    logger.info(json.dumps({
        "event": "request_started",
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "client": request.client.host if request.client else None,
    }))

    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        path_label = _route_label(request)
        HTTP_REQUESTS.labels(method=request.method, path=path_label, status="500").inc()
        HTTP_REQUEST_LATENCY.labels(
            method=request.method, path=path_label
        ).observe(duration_ms / 1000)
        logger.exception(json.dumps({
            "event": "request_failed",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "duration_ms": duration_ms,
            "error": str(exc),
        }))
        raise

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    path_label = _route_label(request)
    HTTP_REQUESTS.labels(
        method=request.method, path=path_label, status=str(response.status_code)
    ).inc()
    HTTP_REQUEST_LATENCY.labels(
        method=request.method, path=path_label
    ).observe(duration_ms / 1000)
    logger.info(json.dumps({
        "event": "request_completed",
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status_code": response.status_code,
        "duration_ms": duration_ms,
    }))

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response

@app.get("/health")
@limiter.limit("100/minute")
async def health_check(request: Request):
    """Health check endpoint"""
    return {
        "status": "ok" if client is not None else "degraded",
        "mode": "distributed" if settings.USE_DISTRIBUTED_MODE else "embedded",
        "environment": settings.ENVIRONMENT,
        "result_cache": result_cache.stats(),
    }


@app.post("/upload")
@limiter.limit(f"{settings.UPLOAD_RATE_LIMIT_PER_MINUTE}/minute")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    api_key: str = Depends(verify_api_key)
):
    """
    Upload and process CSV, JSON, or XLSX files

    Requires API key authentication
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Validate file extension
    if not validate_file_extension(file.filename):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format. Allowed: {', '.join(settings.ALLOWED_FILE_EXTENSIONS)}"
        )

    # Read file contents
    contents = await file.read()

    # Validate file size
    if not validate_file_size(len(contents)):
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {settings.UPLOAD_MAX_SIZE_MB}MB"
        )

    # Sanitize table name (strip only the final extension: "q1.sales.csv" -> "q1_sales")
    stem = os.path.splitext(file.filename)[0]
    table_name = stem.lower().replace(" ", "_").replace("-", "_").replace(".", "_")

    if not is_safe_table_name(table_name):
        raise HTTPException(
            status_code=400,
            detail="Invalid table name. Use only letters, numbers, and underscores. Must start with a letter."
        )

    upload_start = time.perf_counter()
    try:
        # Parse file based on extension
        if file.filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents))
        elif file.filename.endswith('.json'):
            df = pd.read_json(io.BytesIO(contents))
        elif file.filename.endswith('.xlsx'):
            df = pd.read_excel(io.BytesIO(contents))
        else:
            raise HTTPException(status_code=400, detail="Unsupported file format")

        # Validate data frame is not empty
        if df.empty:
            raise HTTPException(status_code=400, detail="File contains no data")

        # Limit number of rows for safety
        max_rows = 1_000_000
        if len(df) > max_rows:
            raise HTTPException(
                status_code=413,
                detail=f"Too many rows. Maximum: {max_rows:,} rows"
            )

        # Store as versioned columnar micro-partitions with zone maps
        source_format = os.path.splitext(file.filename.lower())[1].lstrip('.')
        storage = TableStorage(get_client())
        try:
            meta = storage.store_table(table_name, df, source_format=source_format)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        logger.info(f"Successfully uploaded table '{table_name}' "
                    f"({meta['row_count']} rows, {len(meta['partitions'])} partitions, "
                    f"version {meta['version']})")

        if db_metrics:
            db_metrics.record_write(time.perf_counter() - upload_start)

        return {
            "message": f"Successfully processed {meta['row_count']} records",
            "table_name": table_name,
            "columns": meta["columns"],
            "row_count": meta["row_count"],
            "version": meta["version"],
        }

    except HTTPException:
        raise
    except pd.errors.EmptyDataError:
        raise HTTPException(status_code=400, detail="File is empty")
    except pd.errors.ParserError:
        raise HTTPException(status_code=400, detail="Failed to parse file. Check file format.")
    except Exception as e:
        logger.error(f"Error processing file: {e}")
        raise HTTPException(status_code=500, detail="Error processing file")


class QueryRequest(BaseModel):
    query: str
    query_id: Optional[str] = None
    use_cache: bool = True


class TableContext(BaseModel):
    name: str
    columns: List[str]
    record_count: Optional[int] = None


class AIAssistRequest(BaseModel):
    mode: Literal["optimize_query", "generate_from_english", "fix_sql_error"]
    user_input: str
    current_query: Optional[str] = None
    last_error: Optional[str] = None
    table_context: List[TableContext] = []


class AIAssistResponse(BaseModel):
    success: bool
    mode: str
    suggested_query: Optional[str] = None
    explanation: Optional[str] = None
    error: Optional[str] = None


AI_MODE_INSTRUCTIONS = {
    "optimize_query": (
        "Optimize the provided SQL query for readability and likely performance while preserving its "
        "intent. Return only a safe SELECT query."
    ),
    "generate_from_english": (
        "Generate a SQL SELECT query from the user's plain-English request. Use only the provided "
        "table names and columns."
    ),
    "fix_sql_error": (
        "Fix the user's failing SQL query using the provided last error. Return only a corrected safe "
        "SELECT query."
    ),
}


def format_table_context(table_context: List[TableContext]) -> str:
    if not table_context:
        return "No table context was provided."

    lines = []
    for table in table_context[:20]:
        columns = ", ".join(table.columns[:50])
        record_count = f" ({table.record_count} rows)" if table.record_count is not None else ""
        lines.append(f"- {table.name}{record_count}: {columns}")

    return "\n".join(lines)


def parse_ai_json_response(content: str) -> Dict[str, str]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("AI response was not valid JSON") from exc

    suggested_query = str(parsed.get("suggested_query", "")).strip()
    explanation = str(parsed.get("explanation", "")).strip()

    if not suggested_query:
        raise ValueError("AI response did not include suggested_query")

    return {
        "suggested_query": suggested_query,
        "explanation": explanation,
    }


async def call_ai_assistant(ai_request: AIAssistRequest) -> Dict[str, str]:
    if not settings.AI_API_KEY:
        raise HTTPException(status_code=503, detail="AI assistant is not configured")

    table_context = format_table_context(ai_request.table_context)
    system_prompt = (
        "You are a SQL assistant for spyMonk-warehouse. You are only allowed to help with exactly "
        "three tasks: optimize_query, generate_from_english, and fix_sql_error. You must return a "
        "single JSON object with keys suggested_query and explanation. The suggested_query must be a "
        "single SELECT statement only. Do not include markdown, code fences, comments, INSERT, UPDATE, "
        "DELETE, DROP, ALTER, PRAGMA, ATTACH, DETACH, CREATE, or multiple statements."
    )
    user_prompt = (
        f"Mode: {ai_request.mode}\n"
        f"Task rule: {AI_MODE_INSTRUCTIONS[ai_request.mode]}\n\n"
        f"Known tables and columns:\n{table_context}\n\n"
        f"Current query:\n{ai_request.current_query or ''}\n\n"
        f"Last SQL error:\n{ai_request.last_error or ''}\n\n"
        f"User input:\n{ai_request.user_input}\n"
    )

    endpoint = f"{settings.AI_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.AI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=settings.AI_TIMEOUT_SECONDS) as http_client:
            response = await http_client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {settings.AI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="AI assistant timed out") from exc
    except httpx.HTTPStatusError as exc:
        logger.warning(f"AI provider rejected request with status {exc.response.status_code}")
        raise HTTPException(status_code=502, detail="AI provider request failed") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="AI provider is unavailable") from exc

    data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return parse_ai_json_response(content)


@app.post("/ai/assist", response_model=AIAssistResponse)
@limiter.limit(f"{settings.RATE_LIMIT_PER_MINUTE}/minute")
async def ai_assist(
    request: Request,
    ai_request: AIAssistRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Assist with exactly three SQL tasks: optimize, generate, or fix.

    Requires API key authentication.
    """
    user_input = ai_request.user_input.strip()
    current_query = (ai_request.current_query or "").strip()
    last_error = (ai_request.last_error or "").strip()

    if len(user_input) > 3000:
        raise HTTPException(status_code=400, detail="AI input is too long")

    if ai_request.mode == "optimize_query" and not current_query and not user_input:
        raise HTTPException(status_code=400, detail="Provide a query to optimize")

    if ai_request.mode == "generate_from_english" and not user_input:
        raise HTTPException(status_code=400, detail="Describe the query you want to generate")

    if ai_request.mode == "fix_sql_error" and not current_query and not user_input:
        raise HTTPException(status_code=400, detail="Provide the failing query or describe the SQL error")

    try:
        ai_result = await call_ai_assistant(ai_request)
        suggested_query = ai_result["suggested_query"]
        is_safe, error_msg = sanitize_sql_query(suggested_query)

        if not is_safe:
            logger.warning("AI assistant returned unsafe SQL")
            return AIAssistResponse(
                success=False,
                mode=ai_request.mode,
                error=f"AI output was rejected: {error_msg}",
            )

        return AIAssistResponse(
            success=True,
            mode=ai_request.mode,
            suggested_query=suggested_query,
            explanation=ai_result.get("explanation", ""),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        logger.warning(f"AI assistant returned invalid response: {exc}")
        return AIAssistResponse(
            success=False,
            mode=ai_request.mode,
            error="AI assistant returned an invalid response",
        )


@app.post("/query")
@limiter.limit(f"{settings.RATE_LIMIT_PER_MINUTE}/minute")
def execute_query(
    request: Request,
    query_request: QueryRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Execute SQL queries on uploaded tables

    Only SELECT queries are allowed for security
    Requires API key authentication
    """
    query = query_request.query.strip()
    query_id = query_request.query_id or str(uuid.uuid4())
    query_start = time.perf_counter()

    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    if len(query) > 10000:
        raise HTTPException(status_code=400, detail="Query too long")

    register_query(query_id)

    # Validate query for SQL injection prevention
    try:
        is_safe, error_msg = sanitize_sql_query(query)
        if not is_safe:
            logger.warning(f"Blocked unsafe query: {query}")
            raise HTTPException(status_code=400, detail=f"Invalid query: {error_msg}")

        # Resolve ALL referenced tables (JOINs, subqueries, CTE bodies)
        db = get_client()
        tables = extract_tables(query)
        if not tables:
            raise HTTPException(
                status_code=400,
                detail="Could not parse table name from query. Ensure 'FROM table_name' is present."
            )
        for t in tables:
            if not is_safe_table_name(t):
                raise HTTPException(status_code=400, detail="Invalid table name in query")

        metas: Dict[str, Any] = {}
        for t in tables:
            meta_bytes = db.get(f"table:{t}:meta".encode())
            if not meta_bytes:
                raise HTTPException(
                    status_code=404,
                    detail=f"Table '{t}' not found. Please upload it first."
                )
            metas[t] = json.loads(meta_bytes.decode())

        # Result cache: key = normalized SQL + version of every table touched
        versions = [(t, metas[t].get("version", 0)) for t in tables]
        cache_key = result_cache.make_key(normalize_sql(query), versions)
        if query_request.use_cache:
            cached = result_cache.get(cache_key)
            if cached is not None:
                payload = dict(cached)
                payload["cache_hit"] = True
                record_query_history(db, tables, query,
                                     payload.get("row_count", 0), cache_hit=True)
                if db_metrics:
                    db_metrics.record_read(time.perf_counter() - query_start)
                return payload

        raise_if_cancelled(query_id)

        # Load data: prune partitions where zone maps prove non-matching
        storage = TableStorage(db)
        partitions_scanned = 0
        partitions_total = 0
        frames: Dict[str, pd.DataFrame] = {}
        for t in tables:
            meta = metas[t]
            if "partitions" in meta:
                preds = (extract_predicates(query)
                         if len(tables) == 1 and settings.PRUNING_ENABLED else {})
                selected, total = select_partitions(meta, preds)
                partitions_scanned += len(selected)
                partitions_total += total
                frames[t] = storage.load_partitions(t, meta, selected)
            else:
                # Legacy row-per-key layout written before partitioned storage
                frames[t] = storage.load_legacy(
                    t, meta, cancel_check=lambda: raise_if_cancelled(query_id))
                partitions_scanned += 1
                partitions_total += 1
            raise_if_cancelled(query_id)

        # Execute query using in-memory SQLite
        conn = sqlite3.connect(':memory:')
        try:
            for t, frame in frames.items():
                frame.to_sql(t, conn, index=False, if_exists='replace')

            # Execute user query with timeout and cancellation checks.
            conn.execute("PRAGMA busy_timeout = 5000")  # 5 second timeout
            conn.set_progress_handler(lambda: 1 if is_query_cancelled(query_id) else 0, 1000)
            result_df = pd.read_sql_query(query, conn)
            result_df = result_df.replace([float("inf"), -float("inf")], None)
            result_df = result_df.astype(object).where(pd.notnull(result_df), None)
            result_records = make_json_safe(result_df.to_dict(orient='records'))
        finally:
            conn.close()

        logger.info(f"Successfully executed query on tables {tables}, returned {len(result_records)} rows")

        payload = {
            "success": True,
            "results": result_records,
            "columns": list(result_df.columns),
            "row_count": len(result_records),
            "table_used": tables[0],
            "tables_used": tables,
            "cache_hit": False,
            "partitions_scanned": partitions_scanned,
            "partitions_total": partitions_total,
        }
        result_cache.put(cache_key, payload)
        record_query_history(db, tables, query, len(result_records), cache_hit=False)
        if db_metrics:
            db_metrics.record_read(time.perf_counter() - query_start)
        return payload

    except QueryCancelled:
        logger.info(f"Query cancelled: {query_id}")
        return {
            "success": False,
            "cancelled": True,
            "error": "Query cancelled"
        }
    except HTTPException:
        raise
    except sqlite3.Error as e:
        logger.error(f"SQL error: {e}")
        if is_query_cancelled(query_id):
            return {
                "success": False,
                "cancelled": True,
                "error": "Query cancelled"
            }
        return {
            "success": False,
            "error": "Query execution failed. Check your SQL syntax."
        }
    except Exception as e:
        logger.error(f"Query execution error: {e}")
        return {
            "success": False,
            "error": "An error occurred while executing the query"
        }
    finally:
        complete_query(query_id)


@app.post("/query/cancel/{query_id}")
@limiter.limit(f"{settings.RATE_LIMIT_PER_MINUTE}/minute")
def cancel_query(
    request: Request,
    query_id: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Request cancellation for a running query.

    Requires API key authentication
    """
    with query_lock:
        if query_id not in active_queries:
            return {
                "success": False,
                "cancelled": False,
                "message": "Query is not running"
            }

        cancelled_queries.add(query_id)

    logger.info(f"Cancellation requested for query: {query_id}")
    return {
        "success": True,
        "cancelled": True,
        "query_id": query_id
    }


@app.get("/tables")
@limiter.limit(f"{settings.RATE_LIMIT_PER_MINUTE}/minute")
async def list_tables(
    request: Request,
    api_key: str = Depends(verify_api_key)
):
    """
    List all available tables

    Requires API key authentication
    """
    tables = []

    try:
        # Scan for table metadata
        results = get_client().scan(b"table:", b"table:~")

        for k, v in results:
            if k.endswith(b":meta"):
                try:
                    table_info = json.loads(v.decode())
                    tables.append(table_info)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to decode metadata for key: {k}")
                    continue

    except Exception as e:
        logger.error(f"Error scanning tables: {e}")
        # Return empty list on error rather than failing
        pass

    return {"tables": tables, "count": len(tables)}


@app.get("/tables/{table_name}")
@limiter.limit(f"{settings.RATE_LIMIT_PER_MINUTE}/minute")
async def table_details(
    request: Request,
    table_name: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Table details: definitions (column -> type), row count, source format,
    and the last queries that touched this table.

    Requires API key authentication
    """
    if not is_safe_table_name(table_name):
        raise HTTPException(status_code=400, detail="Invalid table name")

    db = get_client()
    meta_bytes = db.get(f"table:{table_name}:meta".encode())
    if not meta_bytes:
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")

    meta = json.loads(meta_bytes.decode())
    raw_hist = db.get(_history_key(table_name))
    try:
        last_queries = json.loads(raw_hist.decode()) if raw_hist else []
    except ValueError:
        last_queries = []

    columns = meta.get("columns", [])
    return {
        "name": meta.get("name", table_name),
        "columns": columns,
        "column_count": len(columns),
        "record_count": meta.get("record_count", 0),
        "row_count": meta.get("row_count", meta.get("record_count", 0)),
        "schema": meta.get("schema", {}),
        "source_format": meta.get("source_format"),
        "version": meta.get("version"),
        "uploaded_at": meta.get("uploaded_at"),
        "partition_count": len(meta.get("partitions", [])),
        "last_queries": last_queries[:QUERY_HISTORY_LIMIT],
    }


@app.get("/tables/{table_name}/data")
@limiter.limit(f"{settings.RATE_LIMIT_PER_MINUTE}/minute")
def table_data(
    request: Request,
    table_name: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Full contents of a table for the file viewer. Does NOT run SQL and does
    not touch the result cache or query history.

    Requires API key authentication
    """
    if not is_safe_table_name(table_name):
        raise HTTPException(status_code=400, detail="Invalid table name")

    db = get_client()
    meta_bytes = db.get(f"table:{table_name}:meta".encode())
    if not meta_bytes:
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")

    meta = json.loads(meta_bytes.decode())
    storage = TableStorage(db)
    if "partitions" in meta:
        indexes = [p["idx"] for p in meta.get("partitions", [])]
        df = storage.load_partitions(table_name, meta, indexes)
    else:
        df = storage.load_legacy(table_name, meta)

    df = df.replace([float("inf"), -float("inf")], None)
    df = df.astype(object).where(pd.notnull(df), None)
    records = make_json_safe(df.to_dict(orient='records'))

    return {
        "name": meta.get("name", table_name),
        "columns": meta.get("columns", list(df.columns)),
        "results": records,
        "row_count": len(records),
        "source_format": meta.get("source_format"),
    }


@app.delete("/tables/{table_name}")
@limiter.limit("10/minute")
async def delete_table(
    request: Request,
    table_name: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Delete a table and all its data

    Requires API key authentication
    """
    if not is_safe_table_name(table_name):
        raise HTTPException(status_code=400, detail="Invalid table name")

    # Check if table exists
    db = get_client()
    meta_key = f"table:{table_name}:meta".encode()
    meta_bytes = db.get(meta_key)

    if not meta_bytes:
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")

    try:
        meta = json.loads(meta_bytes.decode())
        records_deleted = TableStorage(db).delete_table(table_name, meta)
        db.delete(_history_key(table_name))

        logger.info(f"Deleted table '{table_name}' with {records_deleted} records")

        return {
            "message": f"Successfully deleted table '{table_name}'",
            "records_deleted": records_deleted
        }

    except Exception as e:
        logger.error(f"Error deleting table: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete table")


# Error handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler to prevent information leakage"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    if settings.ENVIRONMENT == "development":
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc)}
        )
    else:
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal error occurred"}
        )


# Serve frontend static files (if available)
frontend_path = os.getenv("FRONTEND_DIST_PATH", "/app/frontend_dist")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
