"""Collect a metrics snapshot from Prometheus, or from direct probes if Prometheus is unavailable."""
import logging
import math
import shutil
import statistics
import time

import httpx

from .models import Snapshot
from .settings import Settings

log = logging.getLogger("sre_agent")

UP = 'up{job="edge-app"}'
P95 = ('histogram_quantile(0.95, sum by (le)'
       '(rate(edge_request_duration_seconds_bucket{path="/api/status"}[30s])))')
DISK = 'edge_disk_used_ratio'


def disk_ratio(path: str) -> float:
    u = shutil.disk_usage(path)
    return u.used / u.total


def _query(http: httpx.Client, s: Settings, q: str) -> float | None:
    r = http.get(f"{s.prometheus_url}/api/v1/query", params={"query": q})
    r.raise_for_status()
    result = r.json()["data"]["result"]
    if not result:
        return None
    v = float(result[0]["value"][1])
    return None if math.isnan(v) else v


def measure_latency(http: httpx.Client, s: Settings, n: int = 3) -> float:
    """Median latency of n direct requests; a timeout or error counts as 'very slow'."""
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        try:
            http.get(f"{s.app_url}/api/status", timeout=5).raise_for_status()
            samples.append(time.perf_counter() - start)
        except httpx.HTTPError:
            samples.append(math.inf)
    return statistics.median(samples)


def probe_up(http: httpx.Client, s: Settings) -> bool:
    try:
        return http.get(f"{s.app_url}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def probe(http: httpx.Client, s: Settings) -> Snapshot:
    up = probe_up(http, s)
    return Snapshot(up=up, p95_s=measure_latency(http, s, 1) if up else None,
                    disk_ratio=disk_ratio(s.data_dir), source="probe")


def collect(http: httpx.Client, s: Settings) -> Snapshot:
    try:
        up = _query(http, s, UP)
        if up is None:
            raise ValueError("no 'up' series for edge-app")
        p95 = _query(http, s, P95) if up == 1 else None
        disk = _query(http, s, DISK)
        if disk is None:  # stale while the app is down: read the volume ourselves
            disk = disk_ratio(s.data_dir)
        return Snapshot(up=up == 1, p95_s=p95, disk_ratio=disk, source="prometheus")
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("Prometheus unavailable (%s), degrading to direct probes", e)
        return probe(http, s)
