"""
SpyMonk-DB Enterprise: Advanced Features Demo

Demonstrates Phase 2-4 features:
- Paxos replication groups
- SQL query execution
- Schema management
- Distributed directories
- Prometheus metrics
"""

import logging
import json
from pathlib import Path

from spymonk_enterprise.client import SpyMonkClient
from spymonk_enterprise.replication.paxos.paxos_group import PaxosGroup
from spymonk_enterprise.replication.replica_manager import ReplicaManager
from spymonk_enterprise.schema.schema import SchemaRegistry, TableSchema, ColumnDef, ColumnType
from spymonk_enterprise.schema.directory import DirectoryManager
from spymonk_enterprise.sql.parser.sql_parser import SQLParser
from spymonk_enterprise.sql.executor.executor import QueryExecutor
from spymonk_enterprise.observability.metrics.prometheus_exporter import MetricsCollector

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def demo_paxos_replication():
    """Demonstrate Paxos replication groups"""
    print("\n" + "=" * 70)
    print("DEMO 1: Paxos Replication Groups")
    print("=" * 70)

    client = SpyMonkClient("/tmp/spymonk-paxos-demo")
    client.start()

    try:
        # Create replica manager
        replica_mgr = ReplicaManager(
            node_id=client.node_id,
            clock=client.clock
        )

        # Create a Paxos group with 3 replicas
        replicas = [client.node_id, "node2", "node3"]
        group = replica_mgr.create_group(
            group_id="tablet-001",
            replicas=replicas
        )

        print(f"✓ Created Paxos group 'tablet-001' with {len(replicas)} replicas")

        # Elect leader
        elected = group.request_leadership()
        if not elected:
            print(f"✗ Node '{client.node_id}' failed to become leader")
            return
        print(f"✓ Node '{client.node_id}' became leader")

        # Propose some values (replicate mutations)
        mutations = [
            b"mutation-1: INSERT INTO users VALUES (1, 'Alice')",
            b"mutation-2: INSERT INTO users VALUES (2, 'Bob')",
            b"mutation-3: UPDATE users SET name='Charlie' WHERE id=1"
        ]

        for i, mutation in enumerate(mutations):
            result = group.propose(mutation)
            print(f"✓ Replicated mutation {i+1}: {result.value}")

        # Get stats
        stats = group.get_stats()
        print(f"\nPaxos Group Statistics:")
        print(f"  State: {stats['state']}")
        print(f"  Leader: {stats['current_leader']}")
        print(f"  Log length: {stats['log_length']}")
        print(f"  Committed: {stats['committed']}")
        print(f"  Has lease: {stats['has_lease']}")

    finally:
        client.stop()


def demo_schema_management():
    """Demonstrate schema registry and table definitions"""
    print("\n" + "=" * 70)
    print("DEMO 2: Schema Management")
    print("=" * 70)

    # Create schema registry
    registry = SchemaRegistry()

    # Define Users table (Spanner-style)
    users_schema = TableSchema(
        table_name="Users",
        columns=[
            ColumnDef(name="user_id", type=ColumnType.INT64, nullable=False),
            ColumnDef(name="name", type=ColumnType.STRING, max_length=100),
            ColumnDef(name="email", type=ColumnType.STRING, max_length=255),
            ColumnDef(name="created_at", type=ColumnType.TIMESTAMP),
        ],
        primary_key=["user_id"]
    )

    registry.create_table(users_schema)
    print(f"✓ Created table: {users_schema}")

    # Define Photos table (interleaved with Users)
    photos_schema = TableSchema(
        table_name="Photos",
        columns=[
            ColumnDef(name="user_id", type=ColumnType.INT64, nullable=False),
            ColumnDef(name="photo_id", type=ColumnType.INT64, nullable=False),
            ColumnDef(name="url", type=ColumnType.STRING, max_length=500),
        ],
        primary_key=["user_id", "photo_id"],
        parent_table="Users"  # Interleaved!
    )

    registry.create_table(photos_schema)
    print(f"✓ Created interleaved table: {photos_schema}")

    # List all tables
    tables = registry.list_tables()
    print(f"\nRegistered tables: {', '.join(tables)}")

    # Validate a row
    row = {
        "user_id": 123,
        "name": "Alice",
        "email": "alice@example.com",
        "created_at": "2024-01-01T00:00:00Z"
    }

    users_schema.validate_row(row)
    print(f"✓ Row validation passed")

    # Encode primary key
    key = users_schema.encode_primary_key(row)
    print(f"✓ Encoded primary key: {key}")


def demo_directory_management():
    """Demonstrate directory-based sharding"""
    print("\n" + "=" * 70)
    print("DEMO 3: Directory-Based Sharding")
    print("=" * 70)

    dir_mgr = DirectoryManager()

    # Create directories for different key ranges
    # Directory 1: Keys [a, m)
    dir1 = dir_mgr.create_directory(
        directory_id="dir-001",
        key_range=(b"a", b"m"),
        paxos_group_id="group-001",
        replication_factor=3
    )
    print(f"✓ Created directory: {dir1}")

    # Directory 2: Keys [m, z)
    dir2 = dir_mgr.create_directory(
        directory_id="dir-002",
        key_range=(b"m", b"z"),
        paxos_group_id="group-002",
        replication_factor=3
    )
    print(f"✓ Created directory: {dir2}")

    # Find directory for keys
    test_keys = [b"apple", b"banana", b"mango", b"zebra"]

    print(f"\nKey routing:")
    for key in test_keys:
        directory = dir_mgr.find_directory(key)
        if directory:
            print(f"  {key.decode()} → {directory.directory_id} (group: {directory.paxos_group_id})")
        else:
            print(f"  {key.decode()} → Not found")

    # Split directory
    print(f"\nSplitting directory dir-001 at key 'g'...")
    left, right = dir_mgr.split_directory("dir-001", b"g")
    print(f"✓ Created: {left}")
    print(f"✓ Created: {right}")

    # Show all directories
    all_dirs = dir_mgr.get_all_directories()
    print(f"\nAll directories ({len(all_dirs)}):")
    for d in all_dirs:
        print(f"  {d}")


def demo_sql_parsing():
    """Demonstrate SQL parsing"""
    print("\n" + "=" * 70)
    print("DEMO 4: SQL Query Parsing")
    print("=" * 70)

    parser = SQLParser()

    # Example queries
    queries = [
        "SELECT name, email FROM Users WHERE age > 21",
        "INSERT INTO Users (name, email) VALUES ('Alice', 'alice@example.com')",
        "UPDATE Users SET name = 'Bob' WHERE user_id = 123",
        "DELETE FROM Users WHERE user_id = 456",
    ]

    for sql in queries:
        print(f"\nSQL: {sql}")
        try:
            ast = parser.parse(sql)
            print(f"✓ Parsed: {type(ast).__name__}")
            print(f"  AST: {ast}")
        except Exception as e:
            print(f"✗ Parse error: {e}")


def demo_metrics_collection():
    """Demonstrate Prometheus metrics"""
    print("\n" + "=" * 70)
    print("DEMO 5: Metrics Collection (Prometheus)")
    print("=" * 70)

    metrics = MetricsCollector(node_id="demo-node-001")
    print(f"✓ Initialized metrics collector")

    # Simulate some operations
    import time
    import random

    print(f"\nSimulating 100 operations...")

    for i in range(100):
        # Simulate reads
        if random.random() < 0.7:
            latency = random.uniform(0.001, 0.01)  # 1-10ms
            metrics.record_read(latency)
        else:
            # Simulate writes
            latency = random.uniform(0.005, 0.05)  # 5-50ms
            metrics.record_write(latency)

        # Simulate transactions
        if i % 10 == 0:
            txn_type = "read_only" if random.random() < 0.5 else "read_write"
            status = "committed" if random.random() < 0.95 else "aborted"
            latency = random.uniform(0.01, 0.1)
            metrics.record_transaction(txn_type, status, latency)

    # Update gauges
    metrics.set_active_transactions(5)
    metrics.set_clock_uncertainty(25_000_000)  # 25ms
    metrics.set_ntp_offset(1_500_000)  # 1.5ms
    metrics.set_memtable_size(2_097_152)  # 2MB

    print(f"✓ Recorded metrics for 100 operations")
    print(f"\nMetrics are exposed at http://localhost:8000/metrics")
    print(f"(Run Prometheus to scrape and visualize)")


def main():
    """Run all demos"""
    print("\n" + "=" * 70)
    print("SpyMonk-DB Enterprise: Advanced Features Demo")
    print("=" * 70)
    print("\nThis demo showcases Phase 2-4 features:")
    print("- Paxos replication groups")
    print("- Schema management (Spanner-style)")
    print("- Directory-based sharding")
    print("- SQL query parsing")
    print("- Prometheus metrics")
    print()

    try:
        demo_paxos_replication()
        demo_schema_management()
        demo_directory_management()
        demo_sql_parsing()
        demo_metrics_collection()

        print("\n" + "=" * 70)
        print("✓ All demos completed successfully!")
        print("=" * 70)

    except Exception as e:
        logger.error(f"Demo failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
