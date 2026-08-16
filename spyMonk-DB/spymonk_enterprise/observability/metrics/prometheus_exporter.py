"""
Prometheus Metrics Exporter

Exposes metrics for monitoring:
- Query latency
- Throughput (reads/writes per second)
- Transaction statistics
- Replication lag
- Clock uncertainty
"""

from prometheus_client import Counter, Histogram, Gauge, Info
import logging

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    Collect and export Prometheus metrics.

    Usage:
        metrics = MetricsCollector()
        metrics.record_read_latency(0.005)  # 5ms
        metrics.increment_transactions()
    """

    def __init__(self, node_id: str):
        self.node_id = node_id

        # Info
        self.info = Info('spymonk_build', 'SpyMonk-DB build information')
        self.info.info({
            'version': '0.1.0',
            'node_id': node_id
        })

        # Throughput counters
        self.reads_total = Counter(
            'spymonk_reads_total',
            'Total number of read operations',
            ['node_id']
        )

        self.writes_total = Counter(
            'spymonk_writes_total',
            'Total number of write operations',
            ['node_id']
        )

        self.transactions_total = Counter(
            'spymonk_transactions_total',
            'Total number of transactions',
            ['node_id', 'type', 'status']
        )

        # Latency histograms
        self.read_latency = Histogram(
            'spymonk_read_latency_seconds',
            'Read operation latency',
            ['node_id'],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
        )

        self.write_latency = Histogram(
            'spymonk_write_latency_seconds',
            'Write operation latency',
            ['node_id'],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
        )

        self.transaction_latency = Histogram(
            'spymonk_transaction_latency_seconds',
            'Transaction commit latency',
            ['node_id', 'type'],
            buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0]
        )

        # Gauges
        self.active_transactions = Gauge(
            'spymonk_active_transactions',
            'Number of active transactions',
            ['node_id']
        )

        self.clock_uncertainty_ms = Gauge(
            'spymonk_clock_uncertainty_milliseconds',
            'HLC clock uncertainty in milliseconds',
            ['node_id']
        )

        self.ntp_offset_ms = Gauge(
            'spymonk_ntp_offset_milliseconds',
            'NTP offset in milliseconds',
            ['node_id']
        )

        self.memtable_size_bytes = Gauge(
            'spymonk_memtable_size_bytes',
            'MemTable size in bytes',
            ['node_id']
        )

        self.paxos_proposals = Counter(
            'spymonk_paxos_proposals_total',
            'Total Paxos proposals',
            ['node_id', 'group_id', 'status']
        )

        logger.info(f"Initialized metrics collector for node {node_id}")

    # Recording methods

    def record_read(self, latency_seconds: float):
        """Record a read operation"""
        self.reads_total.labels(node_id=self.node_id).inc()
        self.read_latency.labels(node_id=self.node_id).observe(latency_seconds)

    def record_write(self, latency_seconds: float):
        """Record a write operation"""
        self.writes_total.labels(node_id=self.node_id).inc()
        self.write_latency.labels(node_id=self.node_id).observe(latency_seconds)

    def record_transaction(self, txn_type: str, status: str, latency_seconds: float):
        """Record a transaction"""
        self.transactions_total.labels(
            node_id=self.node_id,
            type=txn_type,
            status=status
        ).inc()

        self.transaction_latency.labels(
            node_id=self.node_id,
            type=txn_type
        ).observe(latency_seconds)

    def set_active_transactions(self, count: int):
        """Set current active transaction count"""
        self.active_transactions.labels(node_id=self.node_id).set(count)

    def set_clock_uncertainty(self, uncertainty_ns: int):
        """Set current clock uncertainty"""
        self.clock_uncertainty_ms.labels(node_id=self.node_id).set(
            uncertainty_ns / 1_000_000
        )

    def set_ntp_offset(self, offset_ns: int):
        """Set current NTP offset"""
        self.ntp_offset_ms.labels(node_id=self.node_id).set(
            offset_ns / 1_000_000
        )

    def set_memtable_size(self, size_bytes: int):
        """Set current MemTable size"""
        self.memtable_size_bytes.labels(node_id=self.node_id).set(size_bytes)

    def record_paxos_proposal(self, group_id: str, status: str):
        """Record a Paxos proposal"""
        self.paxos_proposals.labels(
            node_id=self.node_id,
            group_id=group_id,
            status=status
        ).inc()
