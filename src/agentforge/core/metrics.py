"""
Metrics — lightweight Prometheus-compatible metric counters.

No external dependencies — just atomic counters and a
/text/metrics endpoint for Prometheus scraping.

Usage:
    from agentforge.core.metrics import metrics
    metrics.inc("tasks_processed", labels={"agent": "xunyu"})
"""

import json
import threading
import time
from datetime import datetime
from typing import Optional


class MetricsRegistry:
    """Thread-safe metric counters with Prometheus text output."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, dict[str, float]] = {}  # name -> labels_key -> value
        self._gauges: dict[str, float] = {}
        self._start_time = time.time()

    def inc(self, name: str, value: int = 1, labels: Optional[dict[str, str]] = None):
        """Increment a counter."""
        labels = labels or {}
        key = _labels_key(labels)
        with self._lock:
            if name not in self._counters:
                self._counters[name] = {}
            self._counters[name][key] = self._counters[name].get(key, 0) + value

    def set_gauge(self, name: str, value: float):
        """Set a gauge value."""
        with self._lock:
            self._gauges[name] = value

    def observe_latency(self, name: str, seconds: float):
        """Record a latency observation (stored as gauge for simplicity)."""
        # Simple approach: store last N latencies as average
        current = self._gauges.get(f"{name}_sum", 0)
        count = self._gauges.get(f"{name}_count", 0)
        with self._lock:
            self._gauges[f"{name}_sum"] = current + seconds
            self._gauges[f"{name}_count"] = count + 1

    def prometheus_text(self) -> str:
        """Render all metrics in Prometheus exposition format."""
        lines = []
        lines.append(f"# HELP agentforge_uptime_seconds Agent uptime in seconds")
        lines.append(f"# TYPE agentforge_uptime_seconds gauge")
        lines.append(f"agentforge_uptime_seconds {time.time() - self._start_time:.1f}")

        with self._lock:
            for name, label_map in sorted(self._counters.items()):
                lines.append(f"# HELP agentforge_{name} AgentForge {name}")
                lines.append(f"# TYPE agentforge_{name} counter")
                for label_key, value in label_map.items():
                    if label_key:
                        lines.append(f"agentforge_{name}{{{label_key}}} {value:.0f}")
                    else:
                        lines.append(f"agentforge_{name} {value:.0f}")

            for name, value in sorted(self._gauges.items()):
                lines.append(f"# HELP agentforge_{name} AgentForge {name}")
                lines.append(f"# TYPE agentforge_{name} gauge")
                lines.append(f"agentforge_{name} {value:.2f}")

        return "\n".join(lines) + "\n"


def _labels_key(labels: dict[str, str]) -> str:
    """Convert labels dict to Prometheus label string."""
    if not labels:
        return ""
    parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
    return ",".join(parts)


# Global singleton
metrics = MetricsRegistry()
