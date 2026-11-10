import dataclasses
import os
from pathlib import Path

from sre_agent.actions import KnownGood, cleanup_logs, rollback_config

MB = 2**20


def make_logs(settings, sizes_mb):
    logs = Path(settings.data_dir) / "logs"
    (logs / "app.log").write_bytes(b"x" * MB)
    for i, size in enumerate(sizes_mb):
        p = logs / f"dump-{i}.log"
        p.write_bytes(b"x" * size * MB)
        os.utime(p, (1000 + i, 1000 + i))  # dump-0 is the oldest
    return logs


def fake_ratio(logs, capacity_mb=20):
    return lambda: sum(p.stat().st_size for p in logs.iterdir()) / (capacity_mb * MB)


def test_cleanup_oldest_first_until_target_then_undo(settings):
    logs = make_logs(settings, [4, 4, 4, 4])  # 17/20 MB = 85 %
    r = cleanup_logs(settings, fake_ratio(logs))
    assert r.ok
    # 85% -> 65% -> 45%: two oldest moved, the rest and the live app.log stay
    assert sorted(p.name for p in logs.iterdir()) == ["app.log", "dump-2.log", "dump-3.log"]
    assert len(list((Path(settings.state_dir) / "quarantine").rglob("*.log"))) == 2
    assert r.undo().ok
    assert len(list(logs.iterdir())) == 5


def test_cleanup_never_touches_live_log(settings):
    logs = make_logs(settings, [])
    r = cleanup_logs(settings, lambda: 0.99)
    assert not r.ok and (logs / "app.log").exists()


def test_cleanup_dry_run_changes_nothing(settings):
    logs = make_logs(settings, [4, 4])
    r = cleanup_logs(dataclasses.replace(settings, dry_run=True), lambda: 0.99)
    assert r.ok and "DRY_RUN" in r.detail and r.undo is None
    assert len(list(logs.iterdir())) == 3


def write_known_good(settings, text):
    good = Path(settings.state_dir) / "known-good" / "app.yaml"
    good.parent.mkdir(parents=True)
    good.write_text(text)


def test_rollback_requires_known_good(settings):
    r = rollback_config(settings)
    assert not r.ok and "no known-good" in r.detail


def test_rollback_refuses_when_config_is_already_good(settings):
    write_known_good(settings, "latency_ms: 0\n")
    r = rollback_config(settings)
    assert not r.ok and "elsewhere" in r.detail


def test_rollback_restores_keeps_mode_and_undo(settings):
    cfg = Path(settings.config_path)
    write_known_good(settings, "latency_ms: 0\n")
    cfg.write_text("latency_ms: 1500\n")
    os.chmod(cfg, 0o640)
    r = rollback_config(settings)
    assert r.ok and cfg.read_text() == "latency_ms: 0\n"
    assert cfg.stat().st_mode & 0o777 == 0o640
    assert list((Path(settings.state_dir) / "backups").iterdir())[0].read_text() == "latency_ms: 1500\n"
    assert r.undo().ok and cfg.read_text() == "latency_ms: 1500\n"


def test_known_good_soak(settings):
    kg, cfg = KnownGood(settings), Path(settings.config_path)
    assert [kg.tick(True) for _ in range(5)] == [None] * 5
    assert kg.tick(True) is not None                      # 6th healthy cycle -> promoted
    assert kg.good.read_text() == "latency_ms: 0\n"

    cfg.write_text("latency_ms: 1500\n")                  # bad config pushed...
    for _ in range(5):
        kg.tick(True)
    kg.tick(False)                                        # ...detected before the soak ends
    for _ in range(5):
        kg.tick(True)
    assert kg.good.read_text() == "latency_ms: 0\n"       # never promoted
