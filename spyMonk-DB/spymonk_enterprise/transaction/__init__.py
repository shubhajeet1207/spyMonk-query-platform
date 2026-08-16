"""
Transaction management for SpyMonk-DB.

Implements distributed transactions with external consistency
using two-phase commit (2PC) and Paxos.
"""

from spymonk_enterprise.transaction.transaction import Transaction, TransactionManager

__all__ = ["Transaction", "TransactionManager"]
