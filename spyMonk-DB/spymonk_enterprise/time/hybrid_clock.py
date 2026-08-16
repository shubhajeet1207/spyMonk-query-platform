"""
Hybrid Logical Clock (HLC) Implementation

Provides a practical alternative to Google Spanner's TrueTime API.
Combines physical time (NTP-synchronized) with logical counters to provide:
- Causality tracking
- Bounded uncertainty
- External consistency guarantees

Reference:
- "Logical Physical Clocks and Consistent Snapshots in Globally Distributed Databases"
  https://cse.buffalo.edu/tech-reports/2014-04.pdf
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Tuple, Optional
import ntplib
import logging

logger = logging.getLogger(__name__)


@dataclass(order=True, frozen=True)
class Timestamp:
    """
    Spanner-style timestamp with uncertainty interval.

    Spanner's TrueTime.now() returns [earliest, latest]
    We use HLC with physical + logical + uncertainty

    Attributes:
        physical: Nanoseconds since epoch (physical time)
        logical: Logical counter for causality
        uncertainty_ns: Clock uncertainty in nanoseconds
    """
    physical: int
    logical: int = field(compare=True)
    uncertainty_ns: int = field(compare=False, default=0)

    def __str__(self) -> str:
        return f"Timestamp({self.physical}.{self.logical}±{self.uncertainty_ns}ns)"

    def __repr__(self) -> str:
        return self.__str__()

    def after(self, other: 'Timestamp') -> bool:
        """Returns True if this timestamp is definitely after other"""
        return self.earliest() > other.latest()

    def before(self, other: 'Timestamp') -> bool:
        """Returns True if this timestamp is definitely before other"""
        return self.latest() < other.earliest()

    def earliest(self) -> int:
        """Earliest possible value (like TrueTime.earliest)"""
        return self.physical - self.uncertainty_ns

    def latest(self) -> int:
        """Latest possible value (like TrueTime.latest)"""
        return self.physical + self.uncertainty_ns

    def overlaps(self, other: 'Timestamp') -> bool:
        """Check if uncertainty intervals overlap"""
        return not (self.after(other) or self.before(other))

    def to_dict(self) -> dict:
        """Serialize to dictionary"""
        return {
            'physical': self.physical,
            'logical': self.logical,
            'uncertainty_ns': self.uncertainty_ns
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Timestamp':
        """Deserialize from dictionary"""
        return cls(
            physical=data['physical'],
            logical=data['logical'],
            uncertainty_ns=data.get('uncertainty_ns', 0)
        )


class HybridLogicalClock:
    """
    Hybrid Logical Clock for distributed timestamps.

    Provides causality tracking and bounded uncertainty without
    requiring GPS or atomic clocks like Spanner's TrueTime.

    Features:
    - NTP/PTP synchronization
    - Logical counters for causality
    - Uncertainty tracking
    - Thread-safe operations

    Typical uncertainty: 20-50ms (vs Spanner's ~7ms)
    This is acceptable for 99% of workloads.
    """

    def __init__(
        self,
        node_id: str,
        ntp_servers: Optional[list] = None,
        max_drift_ns: int = 100_000_000,  # 100ms
        sync_interval_sec: int = 60
    ):
        """
        Initialize HLC.

        Args:
            node_id: Unique identifier for this node
            ntp_servers: List of NTP servers to sync with
            max_drift_ns: Maximum allowed clock drift (default: 100ms)
            sync_interval_sec: How often to sync with NTP (default: 60s)
        """
        self.node_id = node_id
        self.ntp_servers = ntp_servers or ['pool.ntp.org', 'time.google.com']
        self.max_drift_ns = max_drift_ns
        self.sync_interval_sec = sync_interval_sec

        # Thread safety
        self._lock = threading.Lock()

        # HLC state
        self.last_physical = 0
        self.logical = 0

        # NTP sync state
        self.ntp_client = ntplib.NTPClient()
        self.last_ntp_sync = 0
        self.ntp_offset_ns = 0
        self.ntp_uncertainty_ns = 5_000_000  # 5ms default

        # Background NTP sync
        self._sync_thread = None
        self._running = False

        logger.info(f"Initialized HLC for node {node_id}")

    def start(self):
        """Start background NTP synchronization"""
        if self._running:
            return

        self._running = True
        self._sync_thread = threading.Thread(target=self._background_sync, daemon=True)
        self._sync_thread.start()
        logger.info("Started background NTP synchronization")

    def stop(self):
        """Stop background NTP synchronization"""
        self._running = False
        if self._sync_thread:
            self._sync_thread.join(timeout=5)
        logger.info("Stopped background NTP synchronization")

    def now(self) -> Timestamp:
        """
        Returns current timestamp with uncertainty.

        Mimics Spanner's TrueTime.now() API.

        Thread-safe operation that:
        1. Gets physical time (NTP-corrected)
        2. Updates logical counter for causality
        3. Calculates uncertainty bound

        Returns:
            Timestamp with physical, logical, and uncertainty components
        """
        with self._lock:
            # Get physical time (corrected by NTP offset)
            physical = self._physical_time_ns()

            # Update logical counter for causality
            if physical <= self.last_physical:
                # Same physical time: increment logical
                self.logical += 1
            else:
                # New physical time: reset logical
                self.logical = 0
                self.last_physical = physical

            # Calculate uncertainty based on NTP sync quality
            uncertainty = self._calculate_uncertainty()

            return Timestamp(
                physical=physical,
                logical=self.logical,
                uncertainty_ns=uncertainty
            )

    def update(self, msg_timestamp: Timestamp) -> Timestamp:
        """
        Update clock on receiving message (maintains causality).

        This is called when receiving a message from another node
        to ensure happened-before relationships are preserved.

        Args:
            msg_timestamp: Timestamp from received message

        Returns:
            Updated local timestamp
        """
        with self._lock:
            physical = self._physical_time_ns()

            # Take max of local and message time (causality)
            max_physical = max(physical, msg_timestamp.physical)

            if max_physical == self.last_physical == msg_timestamp.physical:
                # All equal: increment logical
                self.logical = max(self.logical, msg_timestamp.logical) + 1
            elif max_physical == self.last_physical:
                # Local time wins
                self.logical += 1
            elif max_physical == msg_timestamp.physical:
                # Message time wins
                self.logical = msg_timestamp.logical + 1
            else:
                # New physical time
                self.logical = 0

            self.last_physical = max_physical
            uncertainty = self._calculate_uncertainty()

            return Timestamp(
                physical=max_physical,
                logical=self.logical,
                uncertainty_ns=uncertainty
            )

    def wait_until(self, ts: Timestamp):
        """
        Wait until timestamp is guaranteed to be in the past.

        Implements Spanner's "commit wait" to ensure external consistency:
        - If T1 commits before T2 starts
        - Then T1's commit_ts < T2's start_ts

        This is the secret sauce for linearizability!

        Args:
            ts: Timestamp to wait for
        """
        while True:
            current = self.now()

            # Wait if there's overlap in uncertainty intervals
            if current.earliest() >= ts.latest():
                break

            # Sleep for the remaining time
            wait_ns = ts.latest() - current.earliest()
            if wait_ns > 0:
                time.sleep(wait_ns / 1_000_000_000)
            else:
                break

    def _physical_time_ns(self) -> int:
        """Get physical time corrected by NTP offset"""
        raw_time = time.time_ns()
        corrected = raw_time + self.ntp_offset_ns
        return corrected

    def _calculate_uncertainty(self) -> int:
        """
        Calculate timestamp uncertainty based on:
        1. NTP sync quality
        2. Time since last sync
        3. Local clock drift

        Spanner uses GPS + atomic clocks: ~7ms uncertainty
        We aim for: <50ms with good NTP sync

        Returns:
            Uncertainty in nanoseconds
        """
        time_since_sync = time.time() - self.last_ntp_sync

        # Uncertainty grows with time since last sync
        # Assume 1ms drift per second (conservative)
        drift_uncertainty = min(
            self.max_drift_ns,
            int(time_since_sync * 1_000_000)
        )

        total_uncertainty = self.ntp_uncertainty_ns + drift_uncertainty
        return total_uncertainty

    def sync_ntp(self) -> bool:
        """
        Synchronize with NTP servers.

        Returns:
            True if sync successful, False otherwise
        """
        for server in self.ntp_servers:
            try:
                response = self.ntp_client.request(server, version=3, timeout=2)

                with self._lock:
                    # Update offset
                    self.ntp_offset_ns = int(response.offset * 1_000_000_000)

                    # Update uncertainty based on round-trip time
                    rtt_ns = int(response.delay * 1_000_000_000)
                    self.ntp_uncertainty_ns = max(rtt_ns // 2, 5_000_000)

                    self.last_ntp_sync = time.time()

                logger.info(
                    f"NTP sync successful: offset={response.offset*1000:.2f}ms, "
                    f"rtt={response.delay*1000:.2f}ms, server={server}"
                )
                return True

            except Exception as e:
                logger.warning(f"NTP sync failed for {server}: {e}")
                continue

        logger.error("All NTP servers failed")
        return False

    def _background_sync(self):
        """Background thread for periodic NTP synchronization"""
        # Initial sync
        self.sync_ntp()

        while self._running:
            time.sleep(self.sync_interval_sec)
            if self._running:
                self.sync_ntp()

    def get_stats(self) -> dict:
        """Get clock statistics"""
        with self._lock:
            return {
                'node_id': self.node_id,
                'last_physical': self.last_physical,
                'logical': self.logical,
                'ntp_offset_ns': self.ntp_offset_ns,
                'ntp_uncertainty_ns': self.ntp_uncertainty_ns,
                'last_ntp_sync': self.last_ntp_sync,
                'time_since_sync': time.time() - self.last_ntp_sync,
                'current_uncertainty': self._calculate_uncertainty(),
            }
