"""
TrueTime facade (Spanner paper, section 3, Table 1).

TT.now() returns an interval [earliest, latest] guaranteed to contain absolute
time. Backed by the NTP-synced HybridLogicalClock instead of GPS/atomic
hardware, so epsilon is larger (>= floor_ns, default 10ms) — correctness
invariants are identical, commit-wait pauses are just longer.
"""

import time
from dataclasses import dataclass

from spymonk_enterprise.time.hybrid_clock import HybridLogicalClock, Timestamp


class ClockUncertaintyError(Exception):
    """Clock uncertainty exceeded the configured ceiling; refusing to serve bounds."""


@dataclass(frozen=True)
class TTInterval:
    earliest: int  # ns since epoch
    latest: int    # ns since epoch


class TrueTime:
    def __init__(self, clock: HybridLogicalClock,
                 floor_ns: int = 10_000_000,
                 ceiling_ns: int = 250_000_000):
        self.clock = clock
        self.floor_ns = floor_ns
        self.ceiling_ns = ceiling_ns

    def now(self) -> TTInterval:
        ts = self.clock.now()
        eps = max(ts.uncertainty_ns, self.floor_ns)
        if eps > self.ceiling_ns:
            raise ClockUncertaintyError(
                f"epsilon {eps}ns exceeds ceiling {self.ceiling_ns}ns; check NTP sync")
        return TTInterval(ts.physical - eps, ts.physical + eps)

    def after(self, ts: Timestamp) -> bool:
        """True iff ts has definitely passed."""
        return self.now().earliest > ts.physical

    def before(self, ts: Timestamp) -> bool:
        """True iff ts has definitely not arrived."""
        return self.now().latest < ts.physical

    def commit_wait(self, s: Timestamp) -> None:
        """Block until TT.after(s) — Spanner's commit wait (section 4.1.2)."""
        while not self.after(s):
            gap_ns = s.physical - self.now().earliest
            self._sleep(max(gap_ns, 100_000) / 1_000_000_000)

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class MockTrueTime(TrueTime):
    """Deterministic TrueTime for tests: manual time, fixed epsilon, no real sleeping."""

    def __init__(self, start_ns: int = 1_000_000_000_000, eps_ns: int = 1_000_000):
        self._now_ns = start_ns
        self.eps_ns = eps_ns
        self.waited: list = []
        self.clock = None
        self.floor_ns = 0
        self.ceiling_ns = float("inf")

    def now(self) -> TTInterval:
        return TTInterval(self._now_ns - self.eps_ns, self._now_ns + self.eps_ns)

    def advance(self, ns: int) -> None:
        self._now_ns += ns

    def _sleep(self, seconds: float) -> None:
        self.waited.append(seconds)
        self.advance(int(seconds * 1_000_000_000))
