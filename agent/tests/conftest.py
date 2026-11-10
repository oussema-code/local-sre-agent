import dataclasses
from pathlib import Path

import pytest

from sre_agent.actions import KnownGood
from sre_agent.approval import ApprovalStore
from sre_agent.graph import Deps, build_graph
from sre_agent.journal import Journal
from sre_agent.models import ActionResult, Snapshot
from sre_agent.policy import PolicyState
from sre_agent.settings import Settings

HEALTHY = Snapshot(up=True, p95_s=0.02, disk_ratio=0.10, source="prometheus")


@pytest.fixture
def settings(tmp_path) -> Settings:
    (tmp_path / "data" / "logs").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.yaml").write_text("latency_ms: 0\n")
    return Settings(data_dir=str(tmp_path / "data"), config_path=str(tmp_path / "config" / "app.yaml"),
                    state_dir=str(tmp_path / "state"), approval_timeout_s=5)


class Recorder:
    """Fake action: records calls, returns a canned result."""

    def __init__(self, result: ActionResult):
        self.result, self.calls = result, 0

    def __call__(self):
        self.calls += 1
        return self.result


@pytest.fixture
def make_agent(settings):
    def make(snapshot: Snapshot, diagnose=lambda i, s: None, verify=(True, "ok"), operator=None, cfg=None,
             **action_results):
        """Build a graph wired with fakes. `operator(store)` is called while waiting for an approval."""
        s = dataclasses.replace(settings, **(cfg or {}))
        state = Path(s.state_dir)
        actions = {name: Recorder(action_results.get(name, ActionResult(True, f"{name} done")))
                   for name in ("restart_service", "cleanup_logs", "rollback_config")}
        store = ApprovalStore(state / "approvals", sleep=lambda _: operator and operator(store))
        deps = Deps(settings=s, collect=lambda: snapshot, diagnose=diagnose, actions=actions,
                    verify=lambda inc: verify, approvals=store,
                    policy_state=PolicyState(state / "state.json"), known_good=KnownGood(s),
                    journal=Journal(state / "incidents.jsonl"))
        return build_graph(deps), deps
    return make
