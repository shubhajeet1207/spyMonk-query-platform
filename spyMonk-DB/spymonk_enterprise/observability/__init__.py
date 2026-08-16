"""
Observability layer for SpyMonk-DB.

Provides:
- Metrics (Prometheus)
- Logging (Structured)
- Tracing (OpenTelemetry)
"""

from spymonk_enterprise.observability.metrics.prometheus_exporter import MetricsCollector

__all__ = ["MetricsCollector"]
