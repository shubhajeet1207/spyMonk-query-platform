"""
Replica Manager

Manages multiple Paxos groups for different tablets.
Coordinates replication across the cluster.
"""

import threading
from typing import Dict, List, Optional
import logging

from spymonk_enterprise.time.hybrid_clock import HybridLogicalClock
from spymonk_enterprise.time.truetime import TrueTime
from spymonk_enterprise.replication.transport import InProcessNetwork
from spymonk_enterprise.replication.paxos.paxos_group import PaxosGroup, ProposeResult

logger = logging.getLogger(__name__)


class ReplicaManager:
    """
    Manages Paxos replication groups.

    Each tablet has its own Paxos group. The replica manager
    coordinates multiple groups on a single server.

    This is a single-process demo/test helper: all groups it creates share
    one in-process network and one TrueTime built from the node's clock.
    A distributed deployment would wire a real transport (e.g. gRPC, Task 8)
    per group instead of the shared InProcessNetwork.
    """

    def __init__(self, node_id: str, clock: HybridLogicalClock):
        self.node_id = node_id
        self.clock = clock

        # Map: group_id -> PaxosGroup
        self.groups: Dict[str, PaxosGroup] = {}

        self._lock = threading.Lock()

        # Shared in-process network + TrueTime for every group this manager creates.
        self._network = InProcessNetwork()
        self._truetime = TrueTime(clock)

        logger.info(f"Initialized replica manager for node {node_id}")

    def create_group(
        self,
        group_id: str,
        replicas: List[str],
        state_machine_callback = None
    ) -> PaxosGroup:
        """
        Create a new Paxos group.

        Args:
            group_id: Unique group identifier
            replicas: List of replica node IDs
            state_machine_callback: Callback for applying committed entries

        Returns:
            New PaxosGroup
        """
        with self._lock:
            if group_id in self.groups:
                raise ValueError(f"Group {group_id} already exists")

            group = PaxosGroup(
                group_id=group_id,
                replica_id=self.node_id,
                replicas=replicas,
                clock=self.clock,
                truetime=self._truetime,
                transport=self._network.transport_for(self.node_id),
                state_machine=state_machine_callback
            )

            self.groups[group_id] = group

            logger.info(f"Created Paxos group {group_id} with {len(replicas)} replicas")
            return group

    def get_group(self, group_id: str) -> Optional[PaxosGroup]:
        """Get Paxos group by ID"""
        return self.groups.get(group_id)

    def replicate(self, group_id: str, value: bytes) -> ProposeResult:
        """
        Replicate a value to a Paxos group.

        Args:
            group_id: Group to replicate to
            value: Serialized mutation

        Returns:
            ProposeResult
        """
        group = self.get_group(group_id)
        if not group:
            raise ValueError(f"Group {group_id} not found")

        return group.propose(value)

    def get_all_stats(self) -> Dict[str, dict]:
        """Get statistics for all groups"""
        return {
            group_id: group.get_stats()
            for group_id, group in self.groups.items()
        }
