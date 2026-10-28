"""Independent, deterministic acceptance checks; no model calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .policy import PolicyError, confined, normalized, sandbox_command
from .store import canonical


LIFECYCLE = (
    "invocation", "input_existence", "input_access", "input_emptiness",
    "malformed_entries", "ties", "aggregation_edges", "output_shape_order",
    "exit_behavior",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_key(data: Any, dotted: str) -> Any:
    for k in dotted.split("."):
        if isinstance(data, list) and k.isdecimal():
            data = data[int(k)]
        elif isinstance(data, dict):
            data = data[k]
        else:
            raise KeyError(dotted)
    return data


class Verifier:
    def __init__(self, root: Path, grader_dir: Path | None = None):
        self.receipts = []
        self.root = root.resolve(strict=True)
        self.grader_dir = grader_dir.resolve(strict=True) if grader_dir else None

    def text(self, p: str) -> str:
        return confined(self.root, p).read_text(encoding="utf-8")

    def json(self, p: str) -> Any:
        return json.loads(self.text(p))

    def embedded_quotes(self, path: str, permitted_sources: list[str],
                        default_source: str | None = None) -> list[str]:
        """Check every structured `quote` field recursively, even absent a grader check."""
        errors = []
        sources = set(permitted_sources)

        def visit(value: Any, label: str) -> None:
            if isinstance(value, list):
                for i, v in enumerate(value):
                    visit(v, f"{label}[{i}]")
            elif isinstance(value, dict):
                if "quote" in value:
                    source = value.get("source", default_source)
                    quote = value["quote"]
                    because = value.get("because")
                    if (source not in sources or not isinstance(quote, str) or not quote or
                        not isinstance(because, str) or not because.strip() or
                            quote not in self.text(source)):
                        errors.append(f"{label}: non-verbatim quote or missing because-clause")
                for k, v in value.items():
                    visit(v, f"{label}.{k}")

        try:
            text = self.text(path)
            if path.endswith(".jsonl"):
                values = [json.loads(line) for line in text.splitlines() if line.strip()]
            elif path.endswith(".json"):
                values = [json.loads(text)]
            else:
                import re
                try:
                    values = [json.loads(text)]
                except ValueError:
                    values = [json.loads(m) for m in re.findall(r"```json\s*(.*?)```", text, re.S)]
            for value in values:
                visit(value, path)
        except (OSError, ValueError, KeyError, PolicyError, TypeError) as exc:
            errors.append(f"{path}: invalid structured evidence: {exc}")
        return errors

    def intake(self, path: str, source: str) -> list[str]:
        errors = []
        try:
            data = self.json(path)
            spec = self.text(source)
            slots = data["slots"]
            if not isinstance(slots, dict) or set(slots) != set(LIFECYCLE):
                return ["intake slots must exactly equal pinned lifecycle matrix"]
            for key in LIFECYCLE:
                item = slots[key]
                if not isinstance(item, dict):
                    errors.append(f"{key}: not an object")
                elif item.get("status") == "citation":
                    if not (isinstance(item.get("quote"), str) and item["quote"] and
                            item["quote"] in spec and isinstance(item.get("because"), str) and
                            item["because"].strip()):
                        errors.append(f"{key}: invalid source quotation or because-clause")
                elif item.get("status") == "gap":
                    if not isinstance(item.get("detail"), str) or not item["detail"].strip():
                        errors.append(f"{key}: empty gap description")
                    else:
                        errors.append(f"{key}: unresolved spec gap: {item['detail']}")
                else:
                    errors.append(f"{key}: must be citation or gap")
        except (OSError, ValueError, TypeError, KeyError, IndexError, PolicyError) as exc:
            errors.append("intake unreadable: " + str(exc))
        return errors

    def check(self, item: dict) -> str | None:
        kind = item.get("type") if isinstance(item, dict) else None
        try:
            from .checks import KINDS, check
            if kind in KINDS:
                return check(self, item)
            if kind == 'obligation-discrepancy-free':
                from .obligations import acknowledged_discrepancy
                doc = self.json(item['path'])
                value = doc.get(item['key'])
                known = {entry['id'] for entry in item['obligations']}
                linked = [entry.get('discrepancy') for entry in doc.get('obligation_resolutions', [])
                          if isinstance(entry, dict) and entry.get('id') in known]
                if not isinstance(value, list) or any(not acknowledged_discrepancy(text, item['obligations']) and text not in linked for text in value):
                    return 'unresolved structured discrepancies:' + item['key']
            elif kind == "discrepancy-free":
                value = self.json(item["path"]).get(item["key"])
                if not isinstance(value, list) or value:
                    return "unresolved structured discrepancies:" + item["key"]
            elif kind == "exists":
                p = confined(self.root, item["path"])
                if not p.is_file() or (item.get("nonempty", True) and p.stat().st_size == 0):
                    return f"{item['path']}: missing or empty"
            elif kind == "sha256":
                if digest(confined(self.root, item["path"])) != item["expected"]:
                    return f"{item['path']}: SHA-256 mismatch"
            elif kind == "verbatim_equal":
                if self.text(item["source"]) != self.text(item["target"]):
                    return f"{item['target']}: verbatim source diff"
            elif kind == "verbatim_contains":
                if self.text(item["source"]) not in self.text(item["target"]):
                    return f"{item['target']}: required source text absent"
            elif kind == "json_count":
                data = get_key(self.json(item["source"]), item["source_key"])
                n = get_key(self.json(item["target"]), item["target_key"])
                if not isinstance(data, list) or type(n) is not int or len(data) != n:
                    return f"{item['target']}: count mismatch"
            elif kind == "json_set_equal":
                left = get_key(self.json(item["source"]), item["source_key"])
                right = get_key(self.json(item["target"]), item["target_key"])
                if not isinstance(left, list) or not isinstance(right, list) or set(map(canonical, left)) != set(map(canonical, right)):
                    return f"{item['target']}: set mismatch"
            elif kind == "citations":
                claims = get_key(self.json(item["path"]), item.get("claims_key", "claims"))
                permitted = set(item["sources"])
                if not isinstance(claims, list) or not claims:
                    return f"{item['path']}: no cited claims"
                for n, claim in enumerate(claims):
                    source = claim.get("source")
                    if (source not in permitted or not isinstance(claim.get("quote"), str) or
                        not claim["quote"] or not isinstance(claim.get("because"), str) or
                            not claim["because"].strip() or claim["quote"] not in self.text(source)):
                        return f"{item['path']}: claim {n} is not verbatim attributed evidence"
            elif kind == "intake":
                errors = self.intake(item["path"], item["source"])
                return "; ".join(errors) if errors else None
            elif kind == "command_receipt":
                data = self.json(item["path"])
                if data.get("exit_code") != 0 or data.get("reason") != "exited":
                    return f"{item['path']}: suite is not green"
            elif kind == "sandbox_acceptance":
                if self.grader_dir is None:
                    return "private verifier directory unavailable"
                private_name = normalized(item["test_file"])
                private_test = confined(self.grader_dir, private_name)
                config = {"argv": item["argv"], "reads": item["reads"],
                          "timeout_seconds": item.get("timeout_seconds", 30),
                          "requires_isolation": item.get("requires_isolation", False)}
                result = sandbox_command(self.root, config, config["timeout_seconds"],
                                         {"grader_hidden_test.py": private_test})
                self.receipts.append(result)
                if result["exit_code"] != 0:
                    return "independent acceptance failed: " + (result["stderr"] or result["stdout"])[-600:]
            elif kind == "json_value":
                if get_key(self.json(item["path"]), item["key"]) != item["expected"]:
                    return f"{item['path']}: pinned value mismatch"
            else:
                return "unsupported verifier check: " + str(kind)
        except (OSError, ValueError, TypeError, KeyError, IndexError, PolicyError) as exc:
            return f"{kind}: cannot check: {exc}"
        return None

    def verify(self, checks: list[dict]) -> dict:
        results = []
        for i, item in enumerate(checks):
            reason = self.check(item)
            results.append({"index": i, "type": item.get("type") if isinstance(item, dict) else None, "passed": reason is None, "error": reason})
        return {"passed": all(r["passed"] for r in results),
                "open_discrepancies": sum(not r["passed"] for r in results),
                "results": results}


def validate_plan(plan: dict, root: Path, grader_file: Path) -> None:
    """Validate coordinator-authored plan, including cross-role immutability."""
    if not grader_file.is_file():
        raise PolicyError("private independent acceptance file not found")
    from .validation import prove
    prove(plan, root, grader_file)
    stages = plan["stages"]
    required = {"INTAKE", "CODER", "INTEGRATOR", "QA", "FIXER", "REVIEW"}
    if set(stages) != required:
        raise PolicyError("plan must define all six stage charters")
    machine = set()
    for role, config in stages.items():
        reads = set(map(normalized, config.get("reads", [])))
        writes = set(map(normalized, config.get("writes", [])))
        deliverables = set(map(normalized, config.get("deliverables", [])))
        commands = config.get("commands", {})
        if not isinstance(commands, dict):
            raise PolicyError("commands must be explicit mapping")
        if role in ("INTEGRATOR", "QA") and not commands:
            raise PolicyError(f"{role} must have a trusted command-backed suite")
        captures = set()
        for name, command in commands.items():
            if not isinstance(name, str) or not name.isidentifier():
                raise PolicyError("bad named command")
            if not command.get("argv"):
                raise PolicyError("empty command")
            cmounts = set(map(normalized, command.get("reads", []) + command.get("writes", [])))
            command_only = set(map(normalized, config.get("command_only", [])))
            from .validation import overlaps
            if not all(any(overlaps(p, s) for s in reads | writes | command_only) for p in cmounts):
                raise PolicyError("command mount outside declared stage files")
            capture = normalized(command["capture"])
            captures.add(capture)
            machine.add(capture)
        from .validation import overlaps
        if not all(any(overlaps(p, s) for s in writes | captures) for p in deliverables):
            raise PolicyError(f"{role}: deliverables must be writable or machine-generated")
        if not captures <= deliverables:
            raise PolicyError(f"{role}: every command receipt must be a declared deliverable")
        if any(p.startswith("vault/") for p in reads | writes | captures):
            raise PolicyError("vault files are harness-owned, not plan-scoped")
        if role == "QA" and set(plan.get("implementation_paths", [])) & (reads | writes):
            raise PolicyError("QA test author may not read or modify implementation")
        if role != "QA" and writes & set(plan.get("qa_test_paths", [])):
            raise PolicyError("only QA may write acceptance tests")
        if role == "INTEGRATOR" and (writes & set(plan.get("implementation_paths", []))) - set(plan.get("wiring_paths", [])):
            raise PolicyError("INTEGRATOR code edits require explicit coordinator-declared wiring paths")
        if writes & ({plan["spec"]} | set(plan.get("immutable_inputs", []))):
            raise PolicyError("no worker may mutate pinned spec or immutable source data")
        for command in commands.values():
            if set(command.get("writes", [])) & ({plan["spec"]} | set(plan.get("immutable_inputs", []))):
                raise PolicyError("command may not mutate pinned spec or immutable source data")
        if not writes and not captures:
            raise PolicyError(f"{role} needs at least one deliverable")
    for role, config in stages.items():
        if set(config.get("writes", [])) & machine:
            raise PolicyError("machine receipts must never be model-writable")
    reviewer_writes = set(stages["REVIEW"].get("writes", []))
    other_deliverables = {p for role, cfg in stages.items() if role != "REVIEW"
                          for p in cfg.get("deliverables", [])}
    if reviewer_writes & other_deliverables:
        raise PolicyError("REVIEW may not rewrite any stage deliverable")
    for p in [plan["spec"], *plan.get("implementation_paths", []), *plan.get("qa_test_paths", [])]:
        normalized(p)
    spec = confined(root, plan["spec"])
    if not spec.is_file():
        raise PolicyError("specification missing")
    if plan["spec"] not in stages["INTAKE"]["reads"]:
        raise PolicyError("INTAKE needs spec")
    if plan["spec"] not in stages["QA"]["reads"]:
        raise PolicyError("QA needs spec to author acceptance tests")
    checks = json.loads(grader_file.read_text(encoding="utf-8"))["checks"]
    if not isinstance(checks, list) or not checks:
        raise PolicyError("at least one independently authored verifier check required")
