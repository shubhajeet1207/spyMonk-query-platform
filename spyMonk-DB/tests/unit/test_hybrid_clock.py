"""
Unit tests for Hybrid Logical Clock.
"""

import pytest
import time
from spymonk_enterprise.time.hybrid_clock import HybridLogicalClock, Timestamp


def test_timestamp_ordering():
    """Test timestamp comparison"""
    ts1 = Timestamp(physical=1000, logical=0, uncertainty_ns=100)
    ts2 = Timestamp(physical=2000, logical=0, uncertainty_ns=100)

    assert ts1 < ts2
    assert ts2 > ts1


def test_timestamp_uncertainty():
    """Test uncertainty intervals"""
    ts = Timestamp(physical=1000, logical=0, uncertainty_ns=100)

    assert ts.earliest() == 900  # 1000 - 100
    assert ts.latest() == 1100   # 1000 + 100


def test_timestamp_overlaps():
    """Test if uncertainty intervals overlap"""
    ts1 = Timestamp(physical=1000, logical=0, uncertainty_ns=200)
    ts2 = Timestamp(physical=1100, logical=0, uncertainty_ns=200)

    # Should overlap: [800, 1200] and [900, 1300]
    assert ts1.overlaps(ts2)

    ts3 = Timestamp(physical=3000, logical=0, uncertainty_ns=100)
    # Should not overlap with ts1
    assert not ts1.overlaps(ts3)


def test_hlc_monotonic():
    """Test that HLC is monotonically increasing"""
    clock = HybridLogicalClock("test-node")

    ts1 = clock.now()
    time.sleep(0.001)  # 1ms
    ts2 = clock.now()
    time.sleep(0.001)
    ts3 = clock.now()

    assert ts1 <= ts2
    assert ts2 <= ts3


def test_hlc_logical_increment():
    """Test logical counter increments within same physical time"""
    clock = HybridLogicalClock("test-node")

    # Get multiple timestamps quickly (same physical time)
    timestamps = [clock.now() for _ in range(5)]

    # Check that logical counters are increasing
    for i in range(1, len(timestamps)):
        if timestamps[i].physical == timestamps[i-1].physical:
            assert timestamps[i].logical > timestamps[i-1].logical


def test_hlc_causality():
    """Test that HLC maintains causality"""
    clock = HybridLogicalClock("test-node")

    # Local event
    ts1 = clock.now()

    # Simulate receiving message from future
    future_ts = Timestamp(
        physical=ts1.physical + 1_000_000_000,  # 1 second in future
        logical=0,
        uncertainty_ns=1_000_000
    )

    # Update clock with future timestamp
    ts2 = clock.update(future_ts)

    # ts2 should be >= future_ts (causality preserved)
    assert ts2.physical >= future_ts.physical


def test_timestamp_serialization():
    """Test timestamp serialization/deserialization"""
    ts = Timestamp(physical=123456, logical=789, uncertainty_ns=1000)

    # Serialize
    data = ts.to_dict()

    # Deserialize
    ts2 = Timestamp.from_dict(data)

    assert ts.physical == ts2.physical
    assert ts.logical == ts2.logical
    assert ts.uncertainty_ns == ts2.uncertainty_ns


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
