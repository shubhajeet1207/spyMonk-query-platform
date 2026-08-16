"""
SpyMonk-DB Enterprise: Spanner-Inspired Distributed Database

A globally-distributed SQL database with external consistency,
inspired by Google Spanner but accessible to everyone.

Key Features:
- External consistency (linearizable transactions)
- Hybrid Logical Clocks (TrueTime alternative)
- Paxos-based replication groups
- Directory-based data model
- Full SQL support with distributed query execution
- Lock-free read-only transactions
"""

__version__ = "0.1.0"
__author__ = "Shubhajeet Pradhan"

from spymonk_enterprise.time.hybrid_clock import HybridLogicalClock, Timestamp
from spymonk_enterprise.client.client import SpyMonkClient

__all__ = [
    "HybridLogicalClock",
    "Timestamp",
    "SpyMonkClient",
]
