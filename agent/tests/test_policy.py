from sre_agent.models import Incident
from sre_agent.policy import PolicyState, evaluate
from sre_agent.settings import Settings

S = Settings()
DOWN = Incident("service_down", 1, "")
SLOW = Incident("high_latency", 2, "")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_allowlist(tmp_path):
    st = PolicyState(tmp_path / "s.json")
    v = evaluate(DOWN, "rollback_config", st, S)
    assert v.action == "escalate" and "not allowed" in v.reason


def test_low_risk_runs_high_risk_needs_approval(tmp_path):
    st = PolicyState(tmp_path / "s.json")
    assert evaluate(DOWN, "restart_service", st, S).needs_approval is False
    v = evaluate(SLOW, "rollback_config", st, S)
    assert v.action == "rollback_config" and v.needs_approval is True


def test_restart_budget_and_window(tmp_path):
    clock = Clock()
    st = PolicyState(tmp_path / "s.json", clock)
    for _ in range(3):
        st.record_restart()
    v = evaluate(DOWN, "restart_service", st, S)
    assert v.action == "escalate" and "budget" in v.reason
    clock.t += 601  # window slides
    assert evaluate(DOWN, "restart_service", st, S).action == "restart_service"


def test_cooldown_expires_and_persists(tmp_path):
    clock = Clock()
    PolicyState(tmp_path / "s.json", clock).start_cooldown("disk_full", 600)
    st = PolicyState(tmp_path / "s.json", clock)  # reloaded from disk
    assert st.cooling() == {"disk_full"}
    clock.t += 601
    assert st.cooling() == set()
