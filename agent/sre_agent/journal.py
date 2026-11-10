"""Persistence helpers: small JSON state files and the append-only incident journal."""
import json
import os
import tempfile
from pathlib import Path


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_json(path: Path, obj) -> None:
    """Atomic write: a crash never leaves a half-written state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(f.name, path)


class Journal:
    def __init__(self, path: Path):
        self.path = path

    def append(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def tail(self, n: int = 10) -> list[dict]:
        try:
            lines = self.path.read_text().splitlines()[-n:]
        except OSError:
            return []
        return [json.loads(line) for line in lines]
