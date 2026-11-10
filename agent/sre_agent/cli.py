"""Operator CLI: python -m sre_agent.cli pending | approve <id> | reject <id> | history"""
import argparse
import getpass
import sys
import time
from pathlib import Path

from .approval import ApprovalStore
from .journal import Journal
from .settings import Settings


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sre_agent.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pending", help="list approval requests waiting for a decision")
    for cmd in ("approve", "reject"):
        sp = sub.add_parser(cmd)
        sp.add_argument("id")
    sp = sub.add_parser("history", help="last incidents from the journal")
    sp.add_argument("-n", type=int, default=10)
    args = p.parse_args(argv)

    state = Path(Settings.from_env().state_dir)
    store = ApprovalStore(state / "approvals")

    if args.cmd == "pending":
        pending = [r for r in store.all() if r["status"] == "pending"]
        for r in pending:
            age = time.time() - r["created"]
            print(f"{r['id']}  {r['action']:<16} {r['incident']['type']:<13} {age:4.0f}s ago  {r['reason']}")
        if not pending:
            print("no pending approval")
    elif args.cmd in ("approve", "reject"):
        try:
            r = store.decide(args.id, "approved" if args.cmd == "approve" else "rejected", by=getpass.getuser())
        except (KeyError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"{r['id']} {r['status']}: {r['action']} for {r['incident']['type']}")
    else:
        for rec in Journal(state / "incidents.jsonl").tail(args.n):
            d = rec.get("decision") or {}
            print(f"{rec['ts']}  {rec['incident']['type']:<13} {d.get('action', '-'):<16} "
                  f"by={d.get('decided_by', '-'):<8} {rec['outcome']:<9} {rec.get('escalation_reason') or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
