from sre_agent.detector import detect
from sre_agent.models import Snapshot
from sre_agent.settings import Settings

S = Settings()


def types(snap):
    return [i.type for i in detect(snap, S)]


def test_healthy():
    assert detect(Snapshot(True, 0.05, 0.3, "prometheus"), S) == []


def test_service_down_is_most_severe_and_hides_latency():
    incs = detect(Snapshot(False, 2.0, 0.9, "prometheus"), S)
    assert [i.type for i in incs] == ["service_down", "disk_full"]
    assert incs[0].severity == 1


def test_latency_threshold_is_strict():
    assert types(Snapshot(True, 0.5, 0.1, "p")) == []
    assert types(Snapshot(True, 0.51, 0.1, "p")) == ["high_latency"]


def test_disk_thresholds_and_severity():
    assert types(Snapshot(True, 0.01, 0.84, "p")) == []
    assert detect(Snapshot(True, 0.01, 0.85, "p"), S)[0].severity == 2
    assert detect(Snapshot(True, 0.01, 0.96, "p"), S)[0].severity == 1


def test_critical_disk_before_latency():
    assert types(Snapshot(True, 1.0, 0.97, "p")) == ["disk_full", "high_latency"]
