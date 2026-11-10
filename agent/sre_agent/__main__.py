"""Run the agent loop: python -m sre_agent [--once]"""
import argparse
import logging
import time
from pathlib import Path

import httpx
from prometheus_client import start_http_server

from . import actions, collector, verify
from .approval import ApprovalStore
from .graph import LAST_CYCLE, Deps, build_graph
from .journal import Journal
from .policy import PolicyState
from .settings import Settings

log = logging.getLogger("sre_agent")


def build_deps(s: Settings) -> Deps:
    http = httpx.Client(timeout=5)
    state = Path(s.state_dir)

    def docker_client():
        import docker  # only the restart action touches the Docker socket
        return docker.from_env()

    return Deps(
        settings=s,
        collect=lambda: collector.collect(http, s),
        diagnose=lambda incident, snap: None,  # phase 2: fixed playbook only
        actions={
            "restart_service": lambda: actions.restart_service(s, docker_client()),
            "cleanup_logs": lambda: actions.cleanup_logs(s),
            "rollback_config": lambda: actions.rollback_config(s),
        },
        verify=lambda incident: verify.verify(incident, http, s, time.sleep),
        approvals=ApprovalStore(state / "approvals"),
        policy_state=PolicyState(state / "state.json"),
        known_good=actions.KnownGood(s),
        journal=Journal(state / "incidents.jsonl"),
    )


def main() -> None:
    p = argparse.ArgumentParser(prog="sre_agent")
    p.add_argument("--once", action="store_true", help="run a single cycle and exit")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    s = Settings.from_env()
    log.info("sre-agent starting (dry_run=%s, interval=%ss)", s.dry_run, s.interval_s)
    if not args.once:
        start_http_server(s.metrics_port)
    graph = build_graph(build_deps(s))

    while True:
        try:
            graph.invoke({"timeline": []})
        except Exception:  # one failing cycle must never kill the loop
            log.exception("cycle failed")
        LAST_CYCLE.set_to_current_time()
        if args.once:
            return
        time.sleep(s.interval_s)


if __name__ == "__main__":
    main()
