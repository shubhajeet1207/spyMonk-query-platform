# spyMonk-DB

A Google Spanner-inspired distributed database in Python. Package name: **`spymonk-db-enterprise`** (import as `spymonk_enterprise`).

It implements the core Spanner ideas end-to-end: hybrid logical clocks with TrueTime-style commit-wait for external consistency, MVCC storage over a WAL + memtable + SSTable engine, two-phase locking with wound-wait deadlock avoidance, and Paxos-replicated groups — fronted by a gRPC server with token authentication and optional TLS.

Used as the storage engine for [spyMonk-warehouse](../spyMonk-warehouse). Design reference: the Spanner paper (`../spanner.pdf`).

## What's implemented

| Area | Module | Details |
|---|---|---|
| Time | `time/` | Hybrid logical clock with NTP sync; TrueTime-style uncertainty + commit-wait |
| Storage | `storage/` | MVCC store; write-ahead log, memtable, SSTable flush |
| Transactions | `transaction/` | 2PL with wound-wait; read-only txns at `LastTS()`; commit-wait for external consistency |
| Replication | `replication/` | Paxos groups with gRPC transport |
| SQL | `sql/` | Parser + executor for a pragmatic subset: `CREATE TABLE`, single-row `INSERT`, single-table `SELECT`/`UPDATE`/`DELETE` with WHERE/ORDER BY/LIMIT/OFFSET |
| Server | `spanserver/` | gRPC server (`spymonk-server` CLI) with bearer-token auth + optional TLS |
| Clients | `client/` | `SpyMonkClient` (embedded or remote), `DistributedClient` (multi-node failover), `spymonk-cli` REPL |
| Observability | `observability/` | Prometheus metrics collector |
| Schema | `schema/` | Table schema registry, directory manager |

**Known limitations (by design, for now):** the SQL engine is single-table (the warehouse layer provides JOINs), `INSERT` parses one row per statement, and `placement/`/`universe/` (shard placement, multi-region universe management) are empty roadmap stubs.

## Quick start

### Embedded (no server)

```python
from spymonk_enterprise.client import SpyMonkClient

client = SpyMonkClient("/tmp/spymonk-data")
client.start()

client.put(b"user:1", b"alice")
print(client.get(b"user:1"))          # b'alice'

with client.begin_transaction() as txn:   # commits on success, aborts on error
    txn.put(b"user:2", b"bob")

client.stop()
```

### Server + remote client

```bash
export SPYMONK_DB_AUTH_TOKEN="$(openssl rand -hex 32)"
spymonk-server --host 0.0.0.0 --port 50051 --data-dir ./data
```

```python
from spymonk_enterprise.client import SpyMonkClient

client = SpyMonkClient("spymonk://localhost:50051", auth_token="<the same token>")
client.start()
client.put(b"k", b"v")
```

### Distributed client (multi-node failover)

```python
from spymonk_enterprise.client import DistributedClient

client = DistributedClient(
    nodes=["db1:50051", "db2:50051", "db3:50051"],
    auth_token="<shared token>",
)
client.start()
client.put(b"k", b"v")                    # fails over on UNAVAILABLE nodes
txn = client.begin_transaction()          # pinned to the node that accepted it
```

## Security

| Env var | Effect |
|---|---|
| `SPYMONK_DB_AUTH_TOKEN` | When set, every RPC must carry `authorization: Bearer <token>`; otherwise the server logs a prominent *unauthenticated* warning |
| `SPYMONK_TLS_CERT` / `SPYMONK_TLS_KEY` | When both set, the server listens with TLS; otherwise it warns that traffic is unencrypted |

Defaults are development-friendly (localhost, no token, no TLS) and loudly warned about. Do not expose an unauthenticated node beyond localhost.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
python -m pytest tests -v        # 173 tests: unit (clock, WAL, SSTable, MVCC,
                                 # locks, txns, Paxos, SQL) + server/client integration
```

Regenerate gRPC stubs after editing `.proto` files: `./scripts/generate_grpc.sh`.

## Packaging & distribution

Metadata lives in `pyproject.toml` (single source of truth; `setup.py` is a shim).

```bash
python -m build                          # dist/spymonk_db_enterprise-*.whl
```

GitLab CI runs the unit suite on every commit and publishes to the GitLab PyPI registry from `main`/tags. For self-hosted distribution see `scripts/private-pypi/` and the suite-level [PACKAGING_GUIDE](../PACKAGING_GUIDE.md).

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — component deep-dive
- [docs/README_ENTERPRISE.md](docs/README_ENTERPRISE.md) — feature overview
- [docs/README_BENCHMARK.md](docs/README_BENCHMARK.md) — benchmarks (`benchmarks/`)
- [examples/](examples/) — runnable quickstart + advanced examples

## License

MIT — see [LICENSE](LICENSE).
