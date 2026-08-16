"""
Time synchronization module for SpyMonk-DB.

Implements Hybrid Logical Clocks (HLC) as a practical alternative
to Google Spanner's TrueTime API.
"""

from spymonk_enterprise.time.hybrid_clock import HybridLogicalClock, Timestamp

__all__ = ["HybridLogicalClock", "Timestamp"]
