"""
Replication layer for SpyMonk-DB.

Implements Paxos-based replication with:
- Multi-Paxos for efficiency
- Leader leases to skip prepare phase
- Log replication
- Automatic failover
"""

from spymonk_enterprise.replication.paxos.paxos_group import PaxosGroup
from spymonk_enterprise.replication.replica_manager import ReplicaManager

__all__ = ["PaxosGroup", "ReplicaManager"]
