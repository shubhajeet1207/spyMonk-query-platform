import pytest
import time

from spymonk_enterprise.time.hybrid_clock import HybridLogicalClock, Timestamp
from spymonk_enterprise.time.truetime import TrueTime, MockTrueTime, ClockUncertaintyError


def test_now_interval_applies_floor_when_uncertainty_is_tiny():
    clock = HybridLogicalClock("n1")
    clock.ntp_uncertainty_ns = 1_000       # 1us — far below the floor
    clock.last_ntp_sync = time.time()      # keep drift term ~0 so eps stays tiny
    tt = TrueTime(clock, floor_ns=10_000_000)
    interval = tt.now()
    # eps is clamped up to the floor -> full width is 2 * floor
    assert interval.latest - interval.earliest == 2 * 10_000_000
    assert interval.earliest < interval.latest


def test_after_and_before_per_paper_table1():
    tt = MockTrueTime(start_ns=1_000_000_000, eps_ns=1_000)
    past = Timestamp(physical=999_000_000, logical=0)
    future = Timestamp(physical=1_001_000_000, logical=0)
    assert tt.after(past) is True        # definitely passed
    assert tt.before(future) is True     # definitely not arrived
    assert tt.after(future) is False
    assert tt.before(past) is False


def test_uncertainty_ceiling_raises():
    clock = HybridLogicalClock("n1")
    clock.ntp_uncertainty_ns = 500_000_000  # 500ms — beyond ceiling
    tt = TrueTime(clock, ceiling_ns=250_000_000)
    with pytest.raises(ClockUncertaintyError):
        tt.now()


def test_commit_wait_blocks_until_after_s():
    tt = MockTrueTime(start_ns=1_000_000_000, eps_ns=5_000)
    s = Timestamp(physical=1_000_050_000, logical=0)
    assert tt.after(s) is False
    tt.commit_wait(s)
    assert tt.after(s) is True
    assert len(tt.waited) >= 1
