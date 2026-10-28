import asyncio
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from optimal_harness.engine import Engine
from optimal_harness.model import Completion, LocalChatClient, ModelQueue
from optimal_harness.policy import FileBroker, PolicyError, sandbox_command
from optimal_harness.store import LeaseLost, Store
from optimal_harness.verifier import LIFECYCLE, Verifier


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "word-counter"


class FakeQueue:
    def __init__(self, actions):
        self.actions = deque(actions)
        self.calls = 0

    async def start(self):
        pass

    async def stop(self):
        pass

    async def request(self, prompt, max_tokens, timeout):
        self.calls += 1
        item = self.actions.popleft()
        if callable(item):
            item = item(prompt)
        return Completion(json.dumps(item), 5, 2, 3)


class HarnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "work"
        shutil.copytree(EXAMPLE / "work", self.root)
        self.grader_dir = self.base / "private_grader"
        shutil.copytree(EXAMPLE / "grader", self.grader_dir)
        self.plan = json.loads((EXAMPLE / "plan.json").read_text())
        self.store = Store(self.base / "board.sqlite")

    def test_broker_confinement(self):
        b = FileBroker(self.root, ["spec.md"], ["artifacts/a.txt"])
        self.assertIn("Invocation", b.read("spec.md"))
        with self.assertRaises(PolicyError):
            b.read("../private_grader/acceptance.json")
        with self.assertRaises(PolicyError):
            b.read("fixtures/words.json")
        with self.assertRaises(PolicyError):
            b.write("spec.md", "tampered")
        (self.root / "artifacts" / "escape.txt").symlink_to(self.grader_dir / "acceptance.json")
        with self.assertRaises(PolicyError):
            FileBroker(self.root, [], ["artifacts/escape.txt"]).write("artifacts/escape.txt", "tampered")
        self.assertIn("checks", (self.grader_dir / "acceptance.json").read_text())

    def test_lease_reclaimed_and_fenced(self):
        engine = Engine(self.store, FakeQueue([]), lease_seconds=1)
        engine.submit(self.root, self.plan, self.grader_dir / "acceptance.json")
        old = self.store.claim("first", 1)
        self.assertIsNotNone(old)
        time.sleep(1.1)
        new = self.store.claim("second", 10)
        self.assertEqual(new["id"], old["id"])
        self.assertEqual(new["generation"], old["generation"] + 1)
        with self.assertRaises(LeaseLost):
            self.store.assert_lease(old)
        self.store.assert_lease(new)

    def test_hash_chain_and_append_only_trigger(self):
        self.store.event("sample", {"abc": 1})
        self.assertTrue(self.store.audit()["valid"])
        with sqlite3.connect(self.store.db) as c:
            with self.assertRaises(sqlite3.DatabaseError):
                c.execute("DELETE FROM events")

    def test_independent_verifier_recomputes(self):
        (self.root / "artifacts" / "count.json").write_text('{"count":5,"names":["a","b"]}')
        checks = [
            {"type": "json_count", "source": "fixtures/words.json", "source_key": "", "target": "artifacts/count.json", "target_key": "count"},
            {"type": "json_set_equal", "source": "artifacts/count.json", "source_key": "names", "target": "artifacts/count.json", "target_key": "names"},
            {"type": "sha256", "path": "spec.md", "expected": "wrong"},
        ]
        # Root selection is intentionally explicit: a missing key is a failure.
        checks[0]["source_key"] = "0"
        result = Verifier(self.root).verify(checks)
        self.assertFalse(result["passed"])
        self.assertEqual(result["open_discrepancies"], 2)
        self.assertTrue(result["results"][1]["passed"])

    def test_intake_citations_or_gaps_exact(self):
        spec = (self.root / "spec.md").read_text()
        slots = {}
        for key, line in zip(LIFECYCLE, [ln for ln in spec.splitlines() if ":" in ln]):
            slots[key] = {"status": "citation", "quote": line, "because": "Explicit lifecycle rule."}
        path = self.root / "artifacts" / "intake.json"
        path.write_text(json.dumps({"slots": slots}))
        self.assertEqual(Verifier(self.root).intake("artifacts/intake.json", "spec.md"), [])
        slots["ties"]["quote"] = "a plausible but nonexistent sentence"
        path.write_text(json.dumps({"slots": slots}))
        self.assertIn("ties", "; ".join(Verifier(self.root).intake("artifacts/intake.json", "spec.md")))

    def test_structured_quote_cannot_be_fabricated(self):
        path = self.root / "artifacts" / "review.json"
        path.write_text(json.dumps({"claims": [{"source": "spec.md", "quote": "invented statement",
                                                  "because": "plausible-sounding reason"}]}))
        errors = Verifier(self.root).embedded_quotes("artifacts/review.json", ["spec.md"])
        self.assertEqual(len(errors), 1)
        self.assertIn("non-verbatim", errors[0])

    def test_false_completion_is_cheap_failure(self):
        queue = FakeQueue([{"action": "finish", "summary": "Everything is done!"}])
        engine = Engine(self.store, queue)
        engine.submit(self.root, self.plan, self.grader_dir / "acceptance.json")
        asyncio.run(engine.serve(1))
        self.assertEqual(self.store.project("wordcounter")["status"], "ESCALATED")
        task = self.store.tasks("wordcounter")[0]
        self.assertEqual(task["status"], "FAILED")
        self.assertIn("missing_or_uncheckpointed_deliverables", task["failure"])
        self.assertEqual(queue.calls, 1)
        self.assertTrue(self.store.audit()["valid"])
        run_dir = self.root / "vault" / "runs" / task["id"] / "a1"
        self.assertTrue((run_dir / "evidence.jsonl").is_file())
        self.assertTrue((run_dir / "handoff.json").is_file())

    def test_token_budget_circuit_breaker(self):
        self.plan["stages"]["INTAKE"]["budget"] = {"max_fresh_tokens": 4}
        engine = Engine(self.store, FakeQueue([{"action":"finish","summary":"claimed"}]))
        engine.submit(self.root, self.plan, self.grader_dir / "acceptance.json")
        asyncio.run(engine.serve(1))
        self.assertIn("fresh_tokens_reserve", self.store.tasks("wordcounter")[0]["failure"])

    def test_three_identical_verifier_defects_escalate(self):
        engine = Engine(self.store, FakeQueue([]))
        engine.submit(self.root, self.plan, self.grader_dir / "acceptance.json")
        # Independently held tests cannot pass because no code has been written.
        with patch("optimal_harness.verifier.sandbox_command", return_value={
            "exit_code": 0, "reason": "exited", "stderr": "", "stdout": ""}):
            for _ in range(3):
                engine._gate("wordcounter", self.store.project("wordcounter")["plan"], self.root)
        p = self.store.project("wordcounter")
        self.assertEqual((p["status"], p["repeats"]), ("ESCALATED", 3))
        self.assertEqual(sum(t["stage"] == "FIXER" for t in self.store.tasks("wordcounter")), 2)

    def test_plan_blocks_qa_source_and_fixer_test_edits(self):
        self.plan["stages"]["QA"]["reads"].append("wordfreq.py")
        with self.assertRaises(PolicyError):
            Engine(self.store, FakeQueue([])).submit(self.root, self.plan, self.grader_dir / "acceptance.json")
        self.plan["stages"]["QA"]["reads"].remove("wordfreq.py")
        self.plan["stages"]["FIXER"]["writes"].append("test_acceptance.py")
        with self.assertRaises(PolicyError):
            Engine(self.store, FakeQueue([])).submit(self.root, self.plan, self.grader_dir / "acceptance.json")

    def test_plan_cannot_omit_trusted_suite(self):
        self.plan["stages"]["QA"]["commands"] = {}
        with self.assertRaises(PolicyError):
            Engine(self.store, FakeQueue([])).submit(self.root, self.plan, self.grader_dir / "acceptance.json")

    def test_sandbox_fails_closed_if_unavailable(self):
        (self.root / "wordfreq.py").write_text("print('ok')\n")
        with patch("optimal_harness.policy.shutil.which", return_value=None):
            with self.assertRaises(PolicyError):
                sandbox_command(self.root, {"argv": ["python3", "wordfreq.py"], "reads": ["wordfreq.py"], "requires_isolation": True})

    def test_real_queue_serializes_loopback_calls_and_meters_cache(self):
        lock = threading.Lock()
        counts = {"active": 0, "maximum": 0}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                with lock:
                    counts["active"] += 1
                    counts["maximum"] = max(counts["maximum"], counts["active"])
                size = int(self.headers["Content-Length"])
                payload = json.loads(self.rfile.read(size))
                time.sleep(0.03)
                response = json.dumps({"choices": [{"message": {"content": '{"action":"finish","summary":"ok"}'}}],
                    "usage": {"prompt_tokens": 40, "completion_tokens": 10,
                              "prompt_tokens_details": {"cached_tokens": 30}}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
                with lock:
                    counts["active"] -= 1

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = LocalChatClient(f"http://127.0.0.1:{server.server_address[1]}", "demo")
        queue = ModelQueue(client, self.base / "model.lock")

        async def execute():
            await queue.start()
            try:
                return await asyncio.gather(*(queue.request("small stateless prompt", 100, 5) for _ in range(3)))
            finally:
                await queue.stop()

        replies = asyncio.run(execute())
        self.assertEqual(counts["maximum"], 1)
        self.assertTrue(all(r.fresh_tokens == 20 and r.cached_tokens == 30 for r in replies))

    def test_full_stage_chain_then_external_coordinator_approval(self):
        lines = [ln for ln in (self.root / "spec.md").read_text().splitlines() if ":" in ln]
        audit = {"slots": {name: {"status": "citation", "quote": line, "because": "Explicit rule."}
                           for name, line in zip(LIFECYCLE, lines)}}

        def write(path, content):
            return {"action": "write", "path": path, "data": content}

        def handoff(prompt):
            match = re.search(r"vault/runs/[0-9a-f]{16}/a[0-9]+/handoff.json", prompt)
            self.assertIsNotNone(match)
            return write(match.group(), json.dumps({"status": "ready", "open_discrepancies": [], "summary": "Checkpointed."}))

        def review(stage):
            return [write("artifacts/review.json", {key: value}) for key, value in {"reviewed_stage": stage,
                "counterexamples": ["Try a missing input file"], "observations": [], "unresolved": []}.items()]

        finish = {"action": "finish", "summary": "All declared artifacts were written."}
        actions = [
            write("artifacts/intake.json", json.dumps(audit)), handoff, finish,
            *review("INTAKE"), handoff, finish,
            write("wordfreq.py", "import sys\nprint('placeholder only in mocked test')\n"),
            write("test_unit.py", "import unittest\n"), handoff, finish,
            *review("CODER"), handoff, finish,
            write("artifacts/integration_notes.md", "Wiring inspected."),
            {"action": "command", "name": "suite"}, handoff, finish,
            *review("INTEGRATOR"), handoff, finish,
            write("test_acceptance.py", "import unittest\n"),
            {"action": "command", "name": "acceptance"}, handoff, finish,
            *review("QA"), handoff, finish,
        ]
        queue = FakeQueue(actions)
        engine = Engine(self.store, queue)
        engine.submit(self.root, self.plan, self.grader_dir / "acceptance.json")

        def mocked_sandbox(root, command, max_seconds=30, *args, **kwargs):
            return {"exit_code": 0, "reason": "exited", "seconds": 0.01, "stdout": "mock suite green", "stderr": ""}

        with patch("optimal_harness.runtime.sandbox_command", side_effect=mocked_sandbox), \
             patch("optimal_harness.verifier.sandbox_command", side_effect=mocked_sandbox):
            asyncio.run(engine.serve(1))
        self.assertEqual(self.store.project("wordcounter")["status"], "AWAITING_COORDINATOR")
        self.assertEqual(len(self.store.tasks("wordcounter")), 8)
        self.assertTrue(all(t["status"] == "SUCCEEDED" for t in self.store.tasks("wordcounter")))
        self.assertEqual(queue.calls, 39)
        self.assertTrue(self.store.audit()["valid"])
        with patch("optimal_harness.verifier.sandbox_command", side_effect=mocked_sandbox):
            engine.coordinator_verdict("wordcounter", True, "Ground truth graded independently; accepted.")
        self.assertEqual(self.store.project("wordcounter")["status"], "DONE")

    def test_modified_grader_cannot_change_pass_condition(self):
        engine = Engine(self.store, FakeQueue([]))
        engine.submit(self.root, self.plan, self.grader_dir / "acceptance.json")
        (self.grader_dir / "acceptance.json").write_text('{"checks":[{"type":"exists","path":"spec.md"}]}')
        engine._gate("wordcounter", self.store.project("wordcounter")["plan"], self.root)
        self.assertEqual(self.store.project("wordcounter")["status"], "ESCALATED")

    def test_modified_spec_cannot_change_acceptance(self):
        engine = Engine(self.store, FakeQueue([]))
        engine.submit(self.root, self.plan, self.grader_dir / "acceptance.json")
        (self.root / "spec.md").write_text("A weaker replacement spec.")
        engine._gate("wordcounter", self.store.project("wordcounter")["plan"], self.root)
        self.assertEqual(self.store.project("wordcounter")["status"], "ESCALATED")


if __name__ == "__main__":
    unittest.main()
