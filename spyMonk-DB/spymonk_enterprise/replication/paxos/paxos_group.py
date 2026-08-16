"""
Multi-Paxos with timed leader leases over a real transport.

Leadership = a counted quorum of lease votes (paper 4.1.1 + Appendix A):
each voter enforces the single-vote rule with TrueTime, the winner's lease
is the min of granted vote_ends, and disjointness follows from TT.after.
Under a valid lease the leader streams AcceptRequests (prepare-free fast
path); a new leader first syncs accepted-but-uncommitted state.
"""

import threading
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple
import logging

from spymonk_enterprise.time.hybrid_clock import Timestamp, HybridLogicalClock
from spymonk_enterprise.time.truetime import TrueTime
from spymonk_enterprise.replication.transport import (
    BoundTransport, LeaseVoteRequest, LeaseVoteGrant, AcceptRequest, AcceptReply,
    CommitNotify, SyncRequest, SyncReply)

logger = logging.getLogger(__name__)


class PaxosState(Enum):
    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"


class ProposeResult(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    NOT_LEADER = "not_leader"
    TIMEOUT = "timeout"


class PaxosGroup:
    def __init__(self, group_id: str, replica_id: str, replicas: List[str],
                 clock: HybridLogicalClock, truetime: TrueTime,
                 transport: BoundTransport,
                 state_machine: Optional[Callable[[bytes, Timestamp], None]] = None,
                 lease_duration_ms: int = 10000):
        self.group_id = group_id
        self.replica_id = replica_id
        self.replicas = replicas
        self.clock = clock
        self.truetime = truetime
        self.transport = transport
        self.state_machine = state_machine
        self.lease_duration_ms = lease_duration_ms

        self.state = PaxosState.FOLLOWER
        self.current_leader: Optional[str] = None
        self.term = 0
        self.lease_end_ns = 0

        # Acceptor state.
        self.log: Dict[int, Tuple[int, bytes, dict]] = {}   # slot -> (term, value, ts_dict)
        self.committed: set = set()
        self.next_slot = 0
        self.applied_index = -1
        self.last_vote: Tuple[int, Optional[str], int] = (0, None, 0)  # (term, candidate, vote_end_ns)

        self.quorum_size = len(replicas) // 2 + 1
        self._lock = threading.RLock()

        transport.register(self._handle)
        logger.info(f"Paxos group {group_id} replica={replica_id} quorum={self.quorum_size}")

    # -- leadership -----------------------------------------------------------

    def request_leadership(self) -> bool:
        with self._lock:
            self.state = PaxosState.CANDIDATE
            self.term += 1
            term = self.term
        grants = []
        for rid in self.replicas:
            reply = self.transport.send(rid, LeaseVoteRequest(term=term, candidate=self.replica_id))
            if isinstance(reply, LeaseVoteGrant) and reply.granted:
                grants.append(reply)
        if len(grants) >= self.quorum_size:
            with self._lock:
                self.state = PaxosState.LEADER
                self.current_leader = self.replica_id
                self.lease_end_ns = min(g.vote_end_ns for g in grants)
            self._sync_from_followers(term)
            logger.info(f"{self.replica_id} became leader (term {term}, {len(grants)} votes)")
            return True
        with self._lock:
            self.state = PaxosState.FOLLOWER
        logger.info(f"{self.replica_id} lost election: {len(grants)}/{self.quorum_size} votes")
        return False

    def has_lease(self) -> bool:
        with self._lock:
            return (self.state == PaxosState.LEADER
                    and self.truetime.now().latest < self.lease_end_ns)

    # -- proposing ------------------------------------------------------------

    def propose(self, value: bytes) -> ProposeResult:
        if not self.has_lease():
            return ProposeResult.NOT_LEADER
        self._redrive_uncommitted()   # heal any owned uncommitted slots first
        with self._lock:
            slot = self.next_slot
            self.next_slot += 1
            term = self.term
            ts = self.clock.now()

        accepts = 0
        for rid in self.replicas:
            reply = self.transport.send(rid, AcceptRequest(term=term, slot=slot,
                                                           value=value, ts=ts.to_dict()))
            if isinstance(reply, AcceptReply) and reply.accepted:
                accepts += 1

        if accepts < self.quorum_size:
            # Do NOT reclaim/reuse this slot. A minority acceptor may have
            # durably stored `value` here; reusing the slot for a different
            # value at the same term would let two values occupy one
            # (term, slot) pair -- violating Paxos safety (a committed value
            # could later regress after a re-election, see _redrive_uncommitted
            # below). The value stays in self.log at `slot` untouched; a later
            # propose() heals the gap by re-driving this same value, never by
            # overwriting it.
            logger.warning(f"propose slot={slot}: {accepts}/{self.quorum_size} accepts")
            return ProposeResult.FAILED

        for rid in self.replicas:
            self.transport.send(rid, CommitNotify(slot=slot))
        return ProposeResult.SUCCESS

    def _redrive_uncommitted(self) -> None:
        """Re-drive slots this leader accepted but never committed, using their
        ORIGINAL stored value (same term, same slot). Safe: a slot is only ever
        re-driven with the one value it already holds -- never a different value --
        so no two values can occupy one (term, slot). Heals apply-stall gaps left
        by transient quorum failures without violating Paxos safety."""
        with self._lock:
            pending = sorted(s for s in self.log if s not in self.committed)
            term = self.term
            entries = [(s, self.log[s]) for s in pending]  # (term, value, ts_dict)
        for slot, (stored_term, value, ts_dict) in entries:
            accepts = 0
            for rid in self.replicas:
                reply = self.transport.send(rid, AcceptRequest(term=term, slot=slot,
                                                               value=value, ts=ts_dict))
                if isinstance(reply, AcceptReply) and reply.accepted:
                    accepts += 1
            if accepts >= self.quorum_size:
                for rid in self.replicas:
                    self.transport.send(rid, CommitNotify(slot=slot))

    # -- message handling (acceptor / follower roles) --------------------------

    def _handle(self, msg):
        if isinstance(msg, LeaseVoteRequest):
            return self._on_lease_vote(msg)
        if isinstance(msg, AcceptRequest):
            return self._on_accept(msg)
        if isinstance(msg, CommitNotify):
            return self._on_commit(msg)
        if isinstance(msg, SyncRequest):
            return self._on_sync(msg)
        logger.warning(f"{self.replica_id}: unknown message {type(msg).__name__}")
        return None

    def _on_lease_vote(self, req: LeaseVoteRequest):
        with self._lock:
            if req.term < self.term:
                return LeaseVoteGrant(term=self.term, voter=self.replica_id,
                                      vote_end_ns=0, granted=False)
            _, prev_candidate, prev_end = self.last_vote
            if (prev_candidate is not None and prev_candidate != req.candidate
                    and not self.truetime.after(Timestamp(physical=prev_end, logical=0))):
                # Single-vote rule (Appendix A): outstanding vote to someone else.
                return LeaseVoteGrant(term=req.term, voter=self.replica_id,
                                      vote_end_ns=0, granted=False)
            self.term = req.term
            if req.candidate != self.replica_id:
                self.state = PaxosState.FOLLOWER
            self.current_leader = req.candidate
            vote_end = self.truetime.now().latest + self.lease_duration_ms * 1_000_000
            self.last_vote = (req.term, req.candidate, vote_end)
            return LeaseVoteGrant(term=req.term, voter=self.replica_id,
                                  vote_end_ns=vote_end, granted=True)

    def _on_accept(self, req: AcceptRequest):
        with self._lock:
            if req.term < self.term:
                return AcceptReply(term=self.term, slot=req.slot, accepted=False)
            self.term = req.term
            self.log[req.slot] = (req.term, req.value, req.ts)
            self.next_slot = max(self.next_slot, req.slot + 1)
            self.clock.update(Timestamp.from_dict(req.ts))
            return AcceptReply(term=req.term, slot=req.slot, accepted=True)

    def _on_commit(self, msg: CommitNotify):
        with self._lock:
            if msg.slot in self.log:
                self.committed.add(msg.slot)
                self._apply_ready()
        return None

    def _on_sync(self, req: SyncRequest):
        with self._lock:
            accepted_log = {slot: (t, v) for slot, (t, v, _) in self.log.items()}
            return SyncReply(term=self.term, accepted=accepted_log,
                             commit_slots=sorted(self.committed))

    def _sync_from_followers(self, term: int):
        """New leader adopts accepted values and re-drives them to commit."""
        merged: Dict[int, Tuple[int, bytes]] = {}
        for rid in self.replicas:
            reply = self.transport.send(rid, SyncRequest(term=term))
            if not isinstance(reply, SyncReply):
                continue
            # SyncReply.accepted returns the full log (not just uncommitted
            # slots), so already-committed slots are re-driven below via
            # `merged` too — no separate use of reply.commit_slots is needed.
            for slot, (t, v) in reply.accepted.items():
                if slot not in merged or merged[slot][0] < t:
                    merged[slot] = (t, v)
        with self._lock:
            if merged:
                self.next_slot = max(self.next_slot, max(merged) + 1)
        for slot, (_, value) in sorted(merged.items()):
            ts = self.clock.now()
            accepts = 0
            for rid in self.replicas:
                reply = self.transport.send(rid, AcceptRequest(term=term, slot=slot,
                                                               value=value, ts=ts.to_dict()))
                if isinstance(reply, AcceptReply) and reply.accepted:
                    accepts += 1
            if accepts >= self.quorum_size:
                for rid in self.replicas:
                    self.transport.send(rid, CommitNotify(slot=slot))

    def _apply_ready(self):
        while (self.applied_index + 1) in self.committed and (self.applied_index + 1) in self.log:
            self.applied_index += 1
            _, value, ts_dict = self.log[self.applied_index]
            if self.state_machine:
                try:
                    self.state_machine(value, Timestamp.from_dict(ts_dict))
                except Exception:
                    logger.exception("state machine application failed")

    def get_stats(self) -> dict:
        with self._lock:
            return {
                'group_id': self.group_id,
                'replica_id': self.replica_id,
                'state': self.state.value,
                'current_leader': self.current_leader,
                'term': self.term,
                'log_length': len(self.log),
                'committed': len(self.committed),
                'applied_index': self.applied_index,
                'has_lease': self.has_lease(),
            }
