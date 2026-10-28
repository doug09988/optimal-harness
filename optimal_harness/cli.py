"""Small operator CLI; cloud coordinator acts only at design and final grade."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .engine import Engine
from .model import LocalChatClient, ModelQueue
from .store import Store
from .verifier import Verifier


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="optimal-harness")
    p.add_argument("--db", default="harness.sqlite3", help="durable SQLite board / audit chain")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("submit", help="register a coordinator-designed project")
    s.add_argument("--root", required=True)
    s.add_argument("--plan", required=True)
    s.add_argument("--grader", required=True, help="independent test keys OUTSIDE project root")
    s = sub.add_parser("run", help="run queued agents; serialize only local model calls")
    s.add_argument("--endpoint", default="http://127.0.0.1:8080")
    s.add_argument("--model", required=True)
    s.add_argument("--jobs", type=int, default=1)
    s.add_argument("--forever", action="store_true")
    s = sub.add_parser("serve", help="loopback role-authenticated board API")
    s.add_argument("--auth-file", required=True)
    s.add_argument("--port", type=int, default=8431)
    sub.add_parser("status")
    s = sub.add_parser("tasks")
    s.add_argument("project")
    sub.add_parser("audit")
    s = sub.add_parser("verify", help="independent machine checks with captured verifier receipts")
    s.add_argument("project")
    s = sub.add_parser("grade", help="coordinator verdict after machine gate")
    s.add_argument("project")
    outcome = s.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--approve", action="store_true")
    outcome.add_argument("--reject", action="store_true")
    s.add_argument("--notes-file", required=True, help="unmodified verifier/coordinator notes")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        store = Store(Path(args.db))
        # Submit/grade operations never contact local or cloud model.
        client = LocalChatClient("http://127.0.0.1:8080", "unused")
        queue = ModelQueue(client, Path(args.db).resolve().parent / "model.lock")
        engine = Engine(store, queue)
        if args.command == "submit":
            plan = json.loads(Path(args.plan).read_text())
            print(json.dumps({"project": engine.submit(Path(args.root), plan, Path(args.grader))}))
        elif args.command == "run":
            if args.jobs != 1:
                raise ValueError("one trusted worker job is supported")
            engine.queue = ModelQueue(LocalChatClient(args.endpoint, args.model),
                                      Path(args.db).resolve().parent / "model.lock")
            asyncio.run(engine.serve(jobs=args.jobs, until_idle=not args.forever))
            print(json.dumps(store.projects(), indent=2))
        elif args.command == "serve":
            from .api import BoardAPI
            api = BoardAPI(engine, json.loads(Path(args.auth_file).read_text()))
            server = api.server(args.port)
            try:
                server.serve_forever()
            finally:
                server.server_close()
        elif args.command == "status":
            print(json.dumps(store.projects(), indent=2))
        elif args.command == "tasks":
            print(json.dumps(store.tasks(args.project), indent=2))
        elif args.command == "audit":
            report = store.audit()
            print(json.dumps(report, indent=2))
            return 0 if report["valid"] else 1
        elif args.command == "verify":
            project = store.project(args.project)
            if not Engine._pins_intact(project["plan"], Path(project["root"])):
                raise ValueError("pinned spec, input, or private grader changed")
            private = Path(project["plan"]["private_grader_file"])
            errors = engine.integrity_errors(args.project, Path(project["root"]))
            if errors:
                raise ValueError(";".join(errors))
            checks = json.loads(private.read_text())["checks"] + engine._discrepancy_checks(args.project)
            for role in ("INTEGRATOR", "QA"):
                for command in project["plan"]["stages"][role]["commands"].values():
                    checks.append({"type": "command_receipt", "path": command["capture"]})
            verifier = Verifier(Path(project["root"]), private.parent)
            result = verifier.verify(checks)
            result["receipt_pointers"] = engine._record_verifier_receipts(args.project, Path(project["root"]), verifier.receipts)
            print(json.dumps(result, indent=2))
            return 0 if result["passed"] else 1
        elif args.command == "grade":
            notes = Path(args.notes_file).read_text(encoding="utf-8")
            engine.coordinator_verdict(args.project, args.approve, notes)
            print(json.dumps({"project": args.project, "status": store.project(args.project)["status"]}))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
