"""Authorized remediation actions. Each returns ActionResult(ok, detail, undo)."""
import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable

from .collector import disk_ratio
from .journal import load_json, save_json
from .models import ActionResult
from .settings import Settings


def restart_service(s: Settings, docker_client) -> ActionResult:
    if s.dry_run:
        return ActionResult(True, f"DRY_RUN: would restart container {s.app_container}")
    c = docker_client.containers.get(s.app_container)
    if c.status == "running":
        c.restart(timeout=10)
        return ActionResult(True, f"restarted {s.app_container}")
    c.start()
    return ActionResult(True, f"started {s.app_container} (was {c.status})")


def cleanup_logs(s: Settings, ratio: Callable[[], float] | None = None) -> ActionResult:
    """Move non-live log files, oldest first, to quarantine (another volume) until usage < target."""
    ratio = ratio or (lambda: disk_ratio(s.data_dir))
    logs = Path(s.data_dir) / "logs"
    files = sorted((p for p in logs.iterdir() if p.is_file() and p.name != "app.log"),
                   key=lambda p: p.stat().st_mtime)
    if s.dry_run:
        return ActionResult(True, f"DRY_RUN: would quarantine up to {len(files)} files from {logs}")
    dest = Path(s.state_dir) / "quarantine" / time.strftime("%Y%m%dT%H%M%S")
    moved, freed = [], 0
    for p in files:
        if ratio() < s.cleanup_target:
            break
        dest.mkdir(parents=True, exist_ok=True)
        freed += p.stat().st_size
        shutil.move(p, dest / p.name)
        moved.append(p)
    if not moved:
        return ActionResult(False, "no log file to clean up")

    def undo() -> ActionResult:
        for p in moved:
            shutil.move(dest / p.name, p)
        return ActionResult(True, f"moved {len(moved)} files back from {dest}")

    return ActionResult(True, f"quarantined {len(moved)} files ({freed / 2**20:.0f} MB) to {dest}, "
                              f"usage now {ratio():.0%}", undo)


def atomic_write(path: Path, data: bytes) -> None:
    """Write via temp file + rename, keeping the original owner and permissions."""
    st = path.stat()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chown(f.name, st.st_uid, st.st_gid)
    os.chmod(f.name, st.st_mode & 0o7777)
    os.replace(f.name, path)


def rollback_config(s: Settings) -> ActionResult:
    cfg = Path(s.config_path)
    good = Path(s.state_dir) / "known-good" / "app.yaml"
    if not good.exists():
        return ActionResult(False, "no known-good config yet")
    current, good_bytes = cfg.read_bytes(), good.read_bytes()
    if current == good_bytes:
        return ActionResult(False, "config already equals known-good: the cause is elsewhere")
    if s.dry_run:
        return ActionResult(True, f"DRY_RUN: would restore {good} over {cfg}")
    backup = Path(s.state_dir) / "backups" / f"app.yaml.{time.strftime('%Y%m%dT%H%M%S')}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(current)
    atomic_write(cfg, good_bytes)

    def undo() -> ActionResult:
        atomic_write(cfg, backup.read_bytes())
        return ActionResult(True, f"restored previous config from {backup}")

    return ActionResult(True, f"restored known-good config (backup: {backup})", undo)


class KnownGood:
    """A config becomes known-good after N consecutive healthy cycles with unchanged content (soak time)."""

    def __init__(self, s: Settings):
        self.cfg = Path(s.config_path)
        self.good = Path(s.state_dir) / "known-good" / "app.yaml"
        self.soak_file = Path(s.state_dir) / "soak.json"
        self.needed = s.soak_cycles

    def tick(self, healthy: bool) -> str | None:
        """Call once per cycle. Returns a message when a config gets promoted."""
        try:
            content = self.cfg.read_bytes()
        except OSError:
            return None
        digest = hashlib.sha256(content).hexdigest()
        st = load_json(self.soak_file, {})
        if st.get("hash") != digest or not healthy:
            st = {"hash": digest, "count": 0}
        if healthy:
            st["count"] += 1
        save_json(self.soak_file, st)
        if st["count"] >= self.needed and (not self.good.exists() or self.good.read_bytes() != content):
            self.good.parent.mkdir(parents=True, exist_ok=True)
            self.good.write_bytes(content)
            return f"config promoted to known-good after {st['count']} healthy cycles"
        return None
