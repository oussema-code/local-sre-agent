"""Deterministic incident detection. No LLM here: thresholds are cheap, fast and auditable."""
from .models import Incident, Snapshot
from .settings import Settings


def detect(snap: Snapshot, s: Settings) -> list[Incident]:
    """All current incidents, most severe first."""
    found = []
    if snap.up is False:
        found.append(Incident("service_down", 1, f"edge-app is not up (source={snap.source})"))
    if snap.up and snap.p95_s is not None and snap.p95_s > s.latency_threshold_s:
        found.append(Incident("high_latency", 2, f"p95 latency {snap.p95_s * 1000:.0f} ms > "
                                                 f"{s.latency_threshold_s * 1000:.0f} ms"))
    if snap.disk_ratio is not None and snap.disk_ratio >= s.disk_threshold:
        sev = 1 if snap.disk_ratio >= s.disk_critical else 2
        found.append(Incident("disk_full", sev, f"/data is {snap.disk_ratio:.0%} used "
                                                f"(threshold {s.disk_threshold:.0%})"))
    return sorted(found, key=lambda i: i.severity)  # stable: ties keep the order above
