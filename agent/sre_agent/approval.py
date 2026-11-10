"""File-based human approval: one JSON file per request. No answer in time = not executed."""
import time
import uuid
from pathlib import Path

from .journal import load_json, save_json


class ApprovalStore:
    def __init__(self, directory: Path, clock=time.time, sleep=time.sleep):
        self.dir, self.clock, self.sleep = directory, clock, sleep

    def _path(self, req_id: str) -> Path:
        return self.dir / f"{req_id}.json"

    def request(self, incident: dict, action: str, reason: str) -> str:
        req_id = uuid.uuid4().hex[:8]
        save_json(self._path(req_id), {"id": req_id, "created": self.clock(), "status": "pending",
                                       "incident": incident, "action": action, "reason": reason})
        return req_id

    def get(self, req_id: str) -> dict | None:
        return load_json(self._path(req_id), None)

    def decide(self, req_id: str, status: str, by: str = "operator") -> dict:
        req = self.get(req_id)
        if req is None:
            raise KeyError(f"no approval request {req_id}")
        if req["status"] != "pending":
            raise ValueError(f"request {req_id} is already {req['status']}")
        req.update(status=status, decided_by=by, decided_at=self.clock())
        save_json(self._path(req_id), req)
        return req

    def wait(self, req_id: str, timeout_s: float, poll_s: float = 1.0) -> str:
        """Block until approved/rejected, or mark it expired. Returns the final status."""
        deadline = self.clock() + timeout_s
        while self.clock() < deadline:
            status = self.get(req_id)["status"]
            if status != "pending":
                return status
            self.sleep(poll_s)
        # ponytail: tiny race if the operator answers in this exact instant; a lock file if it matters
        return self.decide(req_id, "expired", by="timeout")["status"]

    def all(self) -> list[dict]:
        reqs = [load_json(p, None) for p in self.dir.glob("*.json")]
        return sorted((r for r in reqs if r), key=lambda r: r["created"])
