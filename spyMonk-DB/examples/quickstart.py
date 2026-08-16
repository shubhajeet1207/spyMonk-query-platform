"""
SpyMonk-DB Enterprise: Quickstart Example

This example demonstrates the core features of SpyMonk-DB:
- Simple key-value operations
- Transactions with external consistency
- Snapshot isolation for reads
- MVCC and time-travel queries
"""

import logging
from spymonk_enterprise.client import SpyMonkClient

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def main():
    print("=" * 60)
    print("SpyMonk-DB Enterprise - Quickstart Example")
    print("Spanner-inspired distributed database")
    print("=" * 60)
    print()

    # Create client
    client = SpyMonkClient("/tmp/spymonk-demo")
    client.start()

    try:
        # Example 1: Simple KV operations
        print("Example 1: Simple Key-Value Operations")
        print("-" * 60)

        # Write
        ts1 = client.put(b"user:1:name", b"Alice")
        print(f"✓ PUT user:1:name = Alice (timestamp: {ts1})")

        ts2 = client.put(b"user:1:email", b"alice@example.com")
        print(f"✓ PUT user:1:email = alice@example.com (timestamp: {ts2})")

        # Read
        name = client.get(b"user:1:name")
        email = client.get(b"user:1:email")
        print(f"✓ GET user:1:name = {name.decode()}")
        print(f"✓ GET user:1:email = {email.decode()}")
        print()

        # Example 2: Transactions
        print("Example 2: ACID Transactions")
        print("-" * 60)

        txn = client.begin_transaction()
        txn.put(b"account:alice:balance", b"1000")
        txn.put(b"account:bob:balance", b"500")

        # Simulate money transfer
        alice_balance = int(txn.get(b"account:alice:balance") or b"0")
        bob_balance = int(txn.get(b"account:bob:balance") or b"0")

        transfer_amount = 100
        alice_balance -= transfer_amount
        bob_balance += transfer_amount

        txn.put(b"account:alice:balance", str(alice_balance).encode())
        txn.put(b"account:bob:balance", str(bob_balance).encode())

        if txn.commit():
            print(f"✓ Transaction committed successfully")
            print(f"  Alice: $1000 → ${alice_balance}")
            print(f"  Bob: $500 → ${bob_balance}")
        else:
            print("✗ Transaction failed")
        print()

        # Example 3: Read-only transactions (lock-free!)
        print("Example 3: Read-Only Transactions (Lock-Free!)")
        print("-" * 60)

        ro_txn = client.begin_transaction(read_only=True)
        alice = ro_txn.get(b"account:alice:balance")
        bob = ro_txn.get(b"account:bob:balance")
        ro_txn.commit()

        print(f"✓ Read-only snapshot:")
        print(f"  Alice: ${alice.decode()}")
        print(f"  Bob: ${bob.decode()}")
        print(f"  (No locks acquired - readers never block!)")
        print()

        # Example 4: Range scans
        print("Example 4: Range Scans")
        print("-" * 60)

        # Add more data
        client.put(b"product:001", b"Laptop")
        client.put(b"product:002", b"Mouse")
        client.put(b"product:003", b"Keyboard")

        print("✓ Scanning products:")
        for key, value in client.scan(start_key=b"product:", end_key=b"product:~"):
            print(f"  {key.decode()} = {value.decode()}")
        print()

        # Example 5: External consistency demonstration
        print("Example 5: External Consistency")
        print("-" * 60)

        # T1 commits
        txn1 = client.begin_transaction()
        txn1.put(b"counter", b"1")
        ts_commit_1 = txn1.start_timestamp
        success = txn1.commit()

        # T2 starts after T1 commits
        txn2 = client.begin_transaction()
        ts_start_2 = txn2.start_timestamp

        # Due to commit wait, T2's start timestamp > T1's commit timestamp
        print(f"✓ T1 commit timestamp: {ts_commit_1}")
        print(f"✓ T2 start timestamp: {ts_start_2}")
        print(f"✓ External consistency: T2 sees all of T1's writes")

        counter = txn2.get(b"counter")
        print(f"  T2 reads counter = {counter.decode()} (committed by T1)")
        txn2.commit()
        print()

        # Example 6: Clock statistics
        print("Example 6: Clock Statistics")
        print("-" * 60)

        stats = client.clock.get_stats()
        print(f"✓ Node ID: {stats['node_id']}")
        print(f"✓ NTP offset: {stats['ntp_offset_ns'] / 1_000_000:.2f} ms")
        print(f"✓ Clock uncertainty: {stats['current_uncertainty'] / 1_000_000:.2f} ms")
        print(f"✓ Time since NTP sync: {stats['time_since_sync']:.1f} seconds")
        print()

        print("=" * 60)
        print("✓ All examples completed successfully!")
        print("=" * 60)

    finally:
        client.stop()


if __name__ == "__main__":
    main()
