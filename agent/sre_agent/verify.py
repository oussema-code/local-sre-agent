"""Post-fix verification with direct probes (Prometheus rate windows lag by ~30 s)."""
import httpx

from .collector import disk_ratio, measure_latency, probe_up
from .models import Incident
from .settings import Settings


def check(incident: Incident, http: httpx.Client, s: Settings) -> tuple[bool, str]:
    if not probe_up(http, s):
        return False, "health check failed"
    if incident.type == "disk_full":
        r = disk_ratio(s.data_dir)
        return r < s.disk_threshold, f"disk {r:.0%}"
    if incident.type == "high_latency":
        lat = measure_latency(http, s, 3)
        return lat < s.latency_threshold_s, f"median latency {lat * 1000:.0f} ms"
    return True, "health 200"


def verify(incident: Incident, http: httpx.Client, s: Settings, sleep) -> tuple[bool, str]:
    for attempt in range(1, s.verify_attempts + 1):
        ok, detail = check(incident, http, s)
        if ok:
            return True, f"{detail} (attempt {attempt})"
        if attempt < s.verify_attempts:
            sleep(s.verify_interval_s)
    return False, f"{detail} after {s.verify_attempts} attempts"
