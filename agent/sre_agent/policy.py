"""The LLM proposes, the policy decides: allowlist, risk, restart budget, cooldowns."""
import time
from dataclasses import dataclass
from pathlib import Path

from .journal import load_json, save_json
from .models import Incident
from .settings import Settings

ALLOWED = {
    "service_down": ["restart_service", "escalate"],
    "disk_full": ["cleanup_logs", "escalate"],
    "high_latency": ["rollback_config", "restart_service", "escalate"],
}
ACTION_HELP = {
    "restart_service": "Restart (or start) the edge-app container. Low risk.",
    "cleanup_logs": "Move rotated/old log files out of /data/logs to quarantine (reversible). Low risk.",
    "rollback_config": "Restore the last known-good config/app.yaml (reversible). HIGH risk: needs human approval.",
    "escalate": "Do nothing automatically; alert a human.",
}
HIGH_RISK = {"rollback_config"}
PLAYBOOK = {"service_down": "restart_service", "disk_full": "cleanup_logs", "high_latency": "rollback_config"}


@dataclass
class Verdict:
    action: str
    needs_approval: bool
    reason: str


class PolicyState:
    """Restart history and per-incident-type cooldowns, persisted so they survive an agent restart."""

    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = path, clock
        self.data = load_json(path, {"restarts": [], "cooldowns": {}})

    def restarts_in_window(self, window_s: float) -> int:
        return sum(1 for t in self.data["restarts"] if t > self.clock() - window_s)

    def record_restart(self) -> None:
        self.data["restarts"] = self.data["restarts"][-20:] + [self.clock()]
        save_json(self.path, self.data)

    def start_cooldown(self, incident_type: str, seconds: float) -> None:
        self.data["cooldowns"][incident_type] = self.clock() + seconds
        save_json(self.path, self.data)

    def cooling(self) -> set[str]:
        now = self.clock()
        return {t for t, until in self.data["cooldowns"].items() if until > now}


def evaluate(incident: Incident, proposed: str, state: PolicyState, s: Settings) -> Verdict:
    if proposed not in ALLOWED[incident.type]:
        return Verdict("escalate", False, f"'{proposed}' is not allowed for {incident.type}")
    if proposed == "restart_service":
        n = state.restarts_in_window(s.restart_window_s)
        if n >= s.restart_budget:
            return Verdict("escalate", False, f"restart budget exhausted ({n}/{s.restart_budget} "
                                              f"in {s.restart_window_s / 60:.0f} min)")
    if proposed in HIGH_RISK:
        return Verdict(proposed, True, f"{proposed} is high risk: human approval required")
    return Verdict(proposed, False, "allowed, low risk")
