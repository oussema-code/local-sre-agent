"""End-to-end cycle scenarios with fakes (no Docker, no Ollama, no Prometheus)."""
from sre_agent.models import ActionResult, Decision, Snapshot

from conftest import HEALTHY

DOWN = Snapshot(up=False, p95_s=None, disk_ratio=0.1, source="prometheus")
SLOW = Snapshot(up=True, p95_s=2.4, disk_ratio=0.1, source="prometheus")
FULL = Snapshot(up=True, p95_s=0.02, disk_ratio=0.94, source="prometheus")


def last(deps):
    return deps.journal.tail(1)[0]


def test_healthy_cycle_writes_nothing(make_agent):
    graph, deps = make_agent(HEALTHY)
    out = graph.invoke({"timeline": []})
    assert out["incident"] is None and deps.journal.tail() == []


def test_service_down_restart_resolved(make_agent):
    graph, deps = make_agent(DOWN)
    graph.invoke({"timeline": []})
    rec = last(deps)
    assert deps.actions["restart_service"].calls == 1
    assert rec["outcome"] == "resolved" and rec["decision"]["decided_by"] == "fallback"
    assert "service_down" in deps.policy_state.cooling()  # settle time, avoids re-triggering on stale metrics


def test_llm_disallowed_action_is_blocked_by_policy(make_agent):
    llm = lambda inc, snap: Decision(action="rollback_config", reason="trust me", confidence=0.9)
    graph, deps = make_agent(DOWN, diagnose=llm)
    graph.invoke({"timeline": []})
    assert last(deps)["outcome"] == "escalated"
    assert all(a.calls == 0 for a in deps.actions.values())


def test_high_latency_rejected_runs_nothing(make_agent):
    reject = lambda store: [store.decide(r["id"], "rejected") for r in store.all() if r["status"] == "pending"]
    graph, deps = make_agent(SLOW, operator=reject)
    graph.invoke({"timeline": []})
    rec = last(deps)
    assert rec["approval"]["status"] == "rejected" and rec["outcome"] == "escalated"
    assert deps.actions["rollback_config"].calls == 0


def test_high_latency_approved_rollback_resolved(make_agent):
    approve = lambda store: [store.decide(r["id"], "approved") for r in store.all() if r["status"] == "pending"]
    graph, deps = make_agent(SLOW, operator=approve)
    graph.invoke({"timeline": []})
    assert deps.actions["rollback_config"].calls == 1 and last(deps)["outcome"] == "resolved"


def test_approval_timeout_expires_safely(make_agent):
    graph, deps = make_agent(SLOW, cfg={"approval_timeout_s": 0})  # nobody answers
    graph.invoke({"timeline": []})
    rec = last(deps)
    assert rec["approval"]["status"] == "expired" and rec["outcome"] == "escalated"
    assert deps.actions["rollback_config"].calls == 0


def test_cleanup_not_fixing_is_undone_and_escalated(make_agent):
    undone = []
    result = ActionResult(True, "moved 3 files", undo=lambda: undone.append(1) or ActionResult(True, "back"))
    graph, deps = make_agent(FULL, verify=(False, "disk 92%"), cleanup_logs=result)
    graph.invoke({"timeline": []})
    rec = last(deps)
    assert undone == [1] and rec["rolled_back"] is True and rec["outcome"] == "escalated"
    assert "disk_full" in deps.policy_state.cooling()


def test_restart_budget_exhausted_escalates(make_agent):
    graph, deps = make_agent(DOWN)
    for _ in range(3):
        deps.policy_state.record_restart()
    graph.invoke({"timeline": []})
    assert deps.actions["restart_service"].calls == 0
    assert "budget" in last(deps)["escalation_reason"]


def test_cooldown_suppresses_next_cycle(make_agent):
    graph, deps = make_agent(DOWN, restart_service=ActionResult(False, "docker error"))
    graph.invoke({"timeline": []})
    graph.invoke({"timeline": []})
    assert len(deps.journal.tail()) == 1 and deps.actions["restart_service"].calls == 1
