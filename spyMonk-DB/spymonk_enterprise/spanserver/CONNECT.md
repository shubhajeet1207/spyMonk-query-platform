# Connecting to SpyMonk-DB

This guide explains how to connect to a running SpyMonk-DB SpanServer from your Python applications.

## 1. Installation

To use the SpyMonk-DB client SDK in your project, install it in your virtual environment:

```bash
# From your project's root, install SpyMonk-DB in editable mode
pip install -e /path/to/spyMonk-DB
```

## 2. Starting the Server

Before connecting, ensure the SpanServer is running:

```bash
# Start the server (default port 50051)
python3 -m spymonk_enterprise.spanserver.server_cli --port 50051 --data-dir ./data
```

## 3. Basic Connection

Use the `SpyMonkClient` with a `spymonk://` connection string.

```python
from spymonk_enterprise import SpyMonkClient

# Initialize and start the client
client = SpyMonkClient("spymonk://localhost:50051")
client.start()

# ... interaction logic ...

# Always stop the client when done
client.stop()
```

## 4. Common Operations

### SQL Execution
```python
# Create a table
client.execute_sql("CREATE TABLE Users (id INT64, name STRING) PRIMARY KEY (id)")

# Insert data
client.execute_sql("INSERT INTO Users (id, name) VALUES (1, 'Alice')")

# Query data
results = client.execute_sql("SELECT * FROM Users")
for row in results:
    print(row[b"name"].decode())
```

### Key-Value API
```python
# Put/Get
client.put(b"my_key", b"my_value")
value = client.get(b"my_key")

# Scan
for key, val in client.scan(start_key=b"a", end_key=b"z"):
    print(f"{key}: {val}")
```

### Transactions
```python
with client.begin_transaction() as txn:
    val = txn.get(b"balance")
    new_balance = int(val) - 100
    txn.put(b"balance", str(new_balance).encode())
    # Auto-commits on exit, or use txn.commit() / txn.rollback()
```

## 5. Troubleshooting
- **Connection Refused**: Ensure the server is started and the port (default 50051) matches.
- **ImportError**: Ensure the `spymonk_enterprise` package is installed in your `PYTHONPATH` or via `pip`.
