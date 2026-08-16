"""
Pluggable transport for Paxos replication.

InProcessNetwork routes messages between replicas in one process (tests can
inject directed link failures). GrpcTransport (Task 8) carries the same
messages across machines — distribution is deployment, not new logic.
"""

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple, Any


@dataclass(frozen=True)
class LeaseVoteRequest:
    term: int
    candidate: str


@dataclass(frozen=True)
class LeaseVoteGrant:
    term: int
    voter: str
    vote_end_ns: int
    granted: bool


@dataclass(frozen=True)
class AcceptRequest:
    term: int
    slot: int
    value: bytes
    ts: dict  # Timestamp.to_dict()


@dataclass(frozen=True)
class AcceptReply:
    term: int
    slot: int
    accepted: bool


@dataclass(frozen=True)
class CommitNotify:
    slot: int


@dataclass(frozen=True)
class SyncRequest:
    term: int


@dataclass(frozen=True)
class SyncReply:
    term: int
    accepted: dict          # slot -> (term, value)
    commit_slots: list      # slots known committed


MESSAGE_TYPES = {cls.__name__: cls for cls in
                 (LeaseVoteRequest, LeaseVoteGrant, AcceptRequest, AcceptReply,
                  CommitNotify, SyncRequest, SyncReply)}


class BoundTransport:
    """A replica's endpoint on a network."""

    def __init__(self, network: 'InProcessNetwork', replica_id: str):
        self._network = network
        self.replica_id = replica_id

    def register(self, handler: Callable[[Any], Any]) -> None:
        self._network._handlers[self.replica_id] = handler

    def send(self, to: str, msg: Any) -> Optional[Any]:
        return self._network.send(self.replica_id, to, msg)


class InProcessNetwork:
    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self.down_links: set = set()   # {(frm, to)} directed failures

    def transport_for(self, replica_id: str) -> BoundTransport:
        return BoundTransport(self, replica_id)

    def send(self, frm: str, to: str, msg: Any) -> Optional[Any]:
        if (frm, to) in self.down_links or to not in self._handlers:
            return None
        return self._handlers[to](msg)
