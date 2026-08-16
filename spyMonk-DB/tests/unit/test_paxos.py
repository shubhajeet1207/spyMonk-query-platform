import pytest

from spymonk_enterprise.replication.transport import InProcessNetwork
from spymonk_enterprise.replication.paxos.paxos_group import PaxosGroup, ProposeResult
from spymonk_enterprise.time.hybrid_clock import HybridLogicalClock
from spymonk_enterprise.time.truetime import MockTrueTime


def make_cluster(n=3, lease_ms=10_000):
    net = InProcessNetwork()
    tt = MockTrueTime(start_ns=1_000_000_000_000, eps_ns=1_000_000)
    nodes = {}
    ids = [f"r{i}" for i in range(n)]
    applied = {rid: [] for rid in ids}
    for rid in ids:
        clock = HybridLogicalClock(rid)
        nodes[rid] = PaxosGroup(
            group_id="g1", replica_id=rid, replicas=ids,
            clock=clock, truetime=tt, transport=net.transport_for(rid),
            state_machine=(lambda v, ts, r=rid: applied[r].append(v)),
            lease_duration_ms=lease_ms)
    return net, tt, nodes, applied


def test_regression_three_replica_election_succeeds():
    """P0 #5: with 3 replicas, votes must be counted from real replies."""
    _, _, nodes, _ = make_cluster(3)
    assert nodes["r0"].request_leadership() is True
    assert nodes["r0"].has_lease() is True
    assert nodes["r1"].current_leader == "r0"


def test_propose_replicates_to_all():
    _, _, nodes, applied = make_cluster(3)
    nodes["r0"].request_leadership()
    assert nodes["r0"].propose(b"mutation-1") == ProposeResult.SUCCESS
    assert nodes["r0"].propose(b"mutation-2") == ProposeResult.SUCCESS
    for rid in applied:
        assert applied[rid] == [b"mutation-1", b"mutation-2"]


def test_minority_partition_cannot_commit():
    net, _, nodes, _ = make_cluster(3)
    nodes["r0"].request_leadership()
    net.down_links.update({("r0", "r1"), ("r0", "r2")})  # r0 isolated
    assert nodes["r0"].propose(b"x") == ProposeResult.FAILED


def test_lease_expiry_allows_new_leader_only_after_old_lease():
    _, tt, nodes, _ = make_cluster(3, lease_ms=10_000)
    assert nodes["r0"].request_leadership() is True
    # Single-vote rule: r1 cannot win while r0's lease votes are outstanding.
    assert nodes["r1"].request_leadership() is False
    tt.advance(11_000_000_000)  # 11s > lease
    assert nodes["r0"].has_lease() is False
    assert nodes["r1"].request_leadership() is True


def test_single_replica_group_self_elects():
    _, _, nodes, applied = make_cluster(1)
    assert nodes["r0"].request_leadership() is True
    assert nodes["r0"].propose(b"solo") == ProposeResult.SUCCESS
    assert applied["r0"] == [b"solo"]


def test_burned_slot_is_driven_to_completion_not_skipped():
    """A failed propose leaves its value pending at its slot; a later propose
    re-drives it (never reuses the slot for a different value). Safe outcome:
    the burned value commits and applies -- it is NOT silently dropped."""
    net, tt, nodes, applied = make_cluster(3)
    assert nodes["r0"].request_leadership() is True
    assert nodes["r0"].propose(b"A") == ProposeResult.SUCCESS
    net.down_links.update({("r0", "r1"), ("r0", "r2")})   # isolate leader
    assert nodes["r0"].propose(b"B") == ProposeResult.FAILED
    net.down_links.clear()
    assert nodes["r0"].propose(b"C") == ProposeResult.SUCCESS
    # B was accepted on the leader; re-drive commits it. No slot reused.
    for rid in applied:
        assert applied[rid] == [b"A", b"B", b"C"], f"{rid}: {applied[rid]}"


def test_no_value_regression_after_reclaim_scenario():
    """Reviewer's safety repro: a value that fails on a minority must never be
    overwritten by a different value at the same slot; after leadership change the
    originally-accepted value (not a later different one) is what commits."""
    net, tt, nodes, applied = make_cluster(5)
    assert nodes["r0"].request_leadership() is True
    # V reaches only r0 and r1 (a minority incl. a follower) -> FAILED, V pending @ slot0.
    net.down_links.update({("r0", "r2"), ("r0", "r3"), ("r0", "r4")})
    assert nodes["r0"].propose(b"V") == ProposeResult.FAILED
    # Heal to a DIFFERENT quorum excluding r1; propose W. Because slots are never
    # reused, W must NOT land on slot 0 (which holds V); it takes a fresh slot.
    net.down_links.clear()
    result = nodes["r0"].propose(b"W")
    assert result == ProposeResult.SUCCESS
    # slot 0 must hold V everywhere it is present -- never W.
    for rid, node in nodes.items():
        if 0 in node.log:
            assert node.log[0][1] == b"V", f"{rid} slot0 = {node.log[0][1]!r}, expected V (no reuse)"
