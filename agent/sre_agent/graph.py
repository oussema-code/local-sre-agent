"""One agent cycle as a LangGraph StateGraph.

collect -> detect -> (healthy: END) | diagnose -> policy -> [approval] -> act -> verify
        -> report (resolved) | [rollback] -> escalate -> report -> END
"""
import logging
import operator
import time
from dataclasses import asdict, dataclass
from typing import Annotated, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from prometheus_client import Counter, Gauge

from .actions import KnownGood
from .approval import ApprovalStore
from .detector import detect
from .journal import Journal
from .models import ActionResult, Decision, Incident, Snapshot
from .policy import PLAYBOOK, PolicyState, evaluate
from .settings import Settings

log = logging.getLogger("sre_agent")

INCIDENTS = Counter("sre_agent_incidents_total", "Handled incidents", ["type", "outcome"])
ACTIONS = Counter("sre_agent_actions_total", "Executed actions", ["action", "result"])
LAST_CYCLE = Gauge("sre_agent_last_cycle_timestamp_seconds", "End of the last agent cycle")


@dataclass
class Deps:
    settings: Settings
    collect: Callable[[], Snapshot]
    diagnose: Callable[[Incident, Snapshot], Optional[Decision]]  # None -> fallback playbook
    actions: dict[str, Callable[[], ActionResult]]
    verify: Callable[[Incident], tuple[bool, str]]
    approvals: ApprovalStore
    policy_state: PolicyState
    known_good: KnownGood
    journal: Journal


class CycleState(TypedDict, total=False):
    started: float
    snapshot: Snapshot
    incident: Optional[Incident]
    decision: Decision
    decided_by: str             # "llm" or "fallback"
    action: str
    needs_approval: bool
    policy_reason: str
    approval: dict
    result: ActionResult
    verified: bool
    verify_detail: str
    rolled_back: bool
    escalation_reason: str
    outcome: str                # "resolved" or "escalated"
    timeline: Annotated[list[str], operator.add]


def _step(node: str, msg: str, level: int = logging.INFO) -> dict:
    log.log(level, "[%s] %s", node, msg)
    return {"timeline": [f"{time.strftime('%H:%M:%S')} {node}: {msg}"]}


def build_graph(deps: Deps):
    s = deps.settings

    def collect_node(st: CycleState):
        snap = deps.collect()
        return {"started": time.time(), "snapshot": snap,
                **_step("collect", f"up={snap.up} p95={snap.p95_s} disk={snap.disk_ratio} via {snap.source}",
                        logging.DEBUG)}

    def detect_node(st: CycleState):
        incidents = detect(st["snapshot"], s)
        # Soak counts real health, so an incident under cooldown still blocks promotion of the config.
        promoted = deps.known_good.tick(healthy=not incidents)
        if promoted:
            log.info("[known-good] %s", promoted)
        cooling = deps.policy_state.cooling()
        active = [i for i in incidents if i.type not in cooling]
        if not active:
            if incidents:
                log.debug("[detect] %s suppressed (cooldown)", [i.type for i in incidents])
            return {"incident": None}
        inc = active[0]
        return {"incident": inc, **_step("detect", f"{inc.type} (severity {inc.severity}): {inc.evidence}",
                                         logging.WARNING)}

    def diagnose_node(st: CycleState):
        inc = st["incident"]
        decision = deps.diagnose(inc, st["snapshot"])
        decided_by = "llm"
        if decision is None:
            decision = Decision(action=PLAYBOOK[inc.type], reason="fixed playbook", confidence=1.0)
            decided_by = "fallback"
        return {"decision": decision, "decided_by": decided_by,
                **_step("diagnose", f"{decided_by} proposes {decision.action} "
                                    f"(confidence {decision.confidence:.2f}): {decision.reason}")}

    def policy_node(st: CycleState):
        v = evaluate(st["incident"], st["decision"].action, deps.policy_state, s)
        out = {"action": v.action, "needs_approval": v.needs_approval, "policy_reason": v.reason,
               **_step("policy", f"{v.action}: {v.reason}")}
        if v.action == "escalate":
            out["escalation_reason"] = v.reason
        return out

    def approval_node(st: CycleState):
        req_id = deps.approvals.request(asdict(st["incident"]), st["action"], st["decision"].reason)
        log.warning("[approval] %s needs approval -> python -m sre_agent.cli approve %s  (or reject %s), "
                    "timeout %.0fs", st["action"], req_id, req_id, s.approval_timeout_s)
        status = deps.approvals.wait(req_id, s.approval_timeout_s)
        out = {"approval": {"id": req_id, "status": status}, **_step("approval", f"request {req_id}: {status}")}
        if status != "approved":
            out["escalation_reason"] = f"approval {status}: action not executed"
        return out

    def act_node(st: CycleState):
        action = st["action"]
        if action == "restart_service":
            deps.policy_state.record_restart()  # count attempts, even failed ones
        try:
            result = deps.actions[action]()
        except Exception as e:  # an action bug must not crash the cycle
            result = ActionResult(False, f"{type(e).__name__}: {e}")
        ACTIONS.labels(action, "ok" if result.ok else "failed").inc()
        out = {"result": result, **_step("act", f"{action} -> {'ok' if result.ok else 'FAILED'}: {result.detail}")}
        if not result.ok:
            out["escalation_reason"] = f"{action} failed: {result.detail}"
        return out

    def verify_node(st: CycleState):
        ok, detail = deps.verify(st["incident"])
        out = {"verified": ok, "verify_detail": detail, **_step("verify", f"{'OK' if ok else 'FAILED'}: {detail}")}
        if ok:
            deps.policy_state.start_cooldown(st["incident"].type, s.settle_s)
            out["outcome"] = "resolved"
        else:
            out["escalation_reason"] = f"verification failed: {detail}"
        return out

    def rollback_node(st: CycleState):
        try:
            r = st["result"].undo()
        except Exception as e:
            r = ActionResult(False, f"{type(e).__name__}: {e}")
        return {"rolled_back": r.ok, **_step("rollback", f"{'ok' if r.ok else 'FAILED'}: {r.detail}",
                                             logging.WARNING)}

    def escalate_node(st: CycleState):
        inc = st["incident"]
        deps.policy_state.start_cooldown(inc.type, s.cooldown_s)
        return {"outcome": "escalated",
                **_step("escalate", f"{inc.type}: {st.get('escalation_reason', '?')} -> human needed, "
                                    f"cooldown {s.cooldown_s / 60:.0f} min", logging.ERROR)}

    def report_node(st: CycleState):
        inc, d, r = st["incident"], st.get("decision"), st.get("result")
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "incident": asdict(inc),
            "snapshot": asdict(st["snapshot"]),
            "decision": {**d.model_dump(), "decided_by": st.get("decided_by")} if d else None,
            "policy": {"action": st.get("action"), "needs_approval": st.get("needs_approval"),
                       "reason": st.get("policy_reason")},
            "approval": st.get("approval"),
            "result": {"ok": r.ok, "detail": r.detail} if r else None,
            "verified": st.get("verified"),
            "rolled_back": st.get("rolled_back"),
            "outcome": st["outcome"],
            "escalation_reason": st.get("escalation_reason") if st["outcome"] == "escalated" else None,
            "duration_s": round(time.time() - st["started"], 1),
            "timeline": st["timeline"],
        }
        deps.journal.append(record)
        INCIDENTS.labels(inc.type, st["outcome"]).inc()
        log.info("[report] %s -> %s in %.1fs", inc.type, st["outcome"], record["duration_s"])
        return {}

    g = StateGraph(CycleState)
    for name, fn in [("collect", collect_node), ("detect", detect_node), ("diagnose", diagnose_node),
                     ("policy", policy_node), ("approval", approval_node), ("act", act_node),
                     ("verify", verify_node), ("rollback", rollback_node), ("escalate", escalate_node),
                     ("report", report_node)]:
        g.add_node(name, fn)

    g.add_edge(START, "collect")
    g.add_edge("collect", "detect")
    g.add_conditional_edges("detect", lambda st: "diagnose" if st.get("incident") else END)
    g.add_edge("diagnose", "policy")
    g.add_conditional_edges("policy", lambda st: "escalate" if st["action"] == "escalate"
                            else "approval" if st["needs_approval"] else "act")
    g.add_conditional_edges("approval", lambda st: "act" if st["approval"]["status"] == "approved" else "escalate")
    g.add_conditional_edges("act", lambda st: "verify" if st["result"].ok else "escalate")
    g.add_conditional_edges("verify", lambda st: "report" if st["verified"]
                            else "rollback" if st["result"].undo else "escalate")
    g.add_edge("rollback", "escalate")
    g.add_edge("escalate", "report")
    g.add_edge("report", END)
    return g.compile()
