"""Durable task board, fencing leases and tamper-evident append-only event chain."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator
import uuid


def canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class LeaseLost(RuntimeError):
    pass


class Store:
    def __init__(self, db: Path):
        self.db = db.resolve(strict=False)
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, root TEXT NOT NULL, plan TEXT NOT NULL,
                    status TEXT NOT NULL, cycle INTEGER NOT NULL DEFAULT 0,
                    defect TEXT NOT NULL DEFAULT '', repeats INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    stage TEXT NOT NULL, next_stage TEXT, cycle INTEGER NOT NULL,
                    status TEXT NOT NULL, lease_owner TEXT, lease_until REAL,
                    generation INTEGER NOT NULL DEFAULT 0, brief TEXT NOT NULL,
                    failure TEXT NOT NULL DEFAULT '', FOREIGN KEY(project_id) REFERENCES projects(id)
                );
                CREATE TABLE IF NOT EXISTS defect_counts (
                    project TEXT NOT NULL, defect TEXT NOT NULL, repeats INTEGER NOT NULL,
                    PRIMARY KEY(project,defect)
                );
                CREATE TABLE IF NOT EXISTS attempt_usage (
                    task TEXT NOT NULL, generation INTEGER NOT NULL,
                    calls INTEGER NOT NULL DEFAULT 0, prompt INTEGER NOT NULL DEFAULT 0,
                    cached INTEGER NOT NULL DEFAULT 0, completion INTEGER NOT NULL DEFAULT 0,
                    fresh INTEGER NOT NULL DEFAULT 0, started REAL NOT NULL,
                    PRIMARY KEY(task,generation)
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    task TEXT NOT NULL, generation INTEGER NOT NULL, project TEXT NOT NULL,
                    path TEXT NOT NULL, sha256 TEXT NOT NULL, archive TEXT NOT NULL,
                    PRIMARY KEY(task,generation,path)
                );
                CREATE TABLE IF NOT EXISTS handoffs (
                    task TEXT NOT NULL, generation INTEGER NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(task,generation)
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL,
                    previous TEXT NOT NULL, digest TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS no_event_update BEFORE UPDATE ON events
                  BEGIN SELECT RAISE(ABORT, 'append-only event log'); END;
                CREATE TRIGGER IF NOT EXISTS no_event_delete BEFORE DELETE ON events
                  BEGIN SELECT RAISE(ABORT, 'append-only event log'); END;
            """)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA journal_mode=WAL")
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def _event(c: sqlite3.Connection, kind: str, payload: Any) -> None:
        last = c.execute("SELECT digest FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = last["digest"] if last else "0" * 64
        timestamp = time.time()
        body = canonical(payload)
        digest = hashlib.sha256(canonical([prev, timestamp, kind, body]).encode()).hexdigest()
        c.execute("INSERT INTO events(at,kind,payload,previous,digest) VALUES(?,?,?,?,?)",
                  (timestamp, kind, body, prev, digest))

    def event(self, kind: str, payload: Any) -> None:
        with self.transaction() as c:
            self._event(c, kind, payload)

    def add_project(self, project: str, root: Path, plan: dict, first: dict) -> None:
        with self.transaction() as c:
            c.execute("INSERT INTO projects(id,root,plan,status) VALUES(?,?,?,?)",
                      (project, str(root.resolve()), canonical(plan), "RUNNING"))
            self._task(c, project, "INTAKE", None, 0, first)
            self._event(c, "project_submitted", {"project": project, "plan_sha256": hashlib.sha256(canonical(plan).encode()).hexdigest()})

    @staticmethod
    def _task(c: sqlite3.Connection, project: str, stage: str, next_stage: str | None,
              cycle: int, brief: dict) -> str:
        tid = uuid.uuid4().hex[:16]
        c.execute("INSERT INTO tasks(id,project_id,stage,next_stage,cycle,status,brief) VALUES(?,?,?,?,?,'QUEUED',?)",
                  (tid, project, stage, next_stage, cycle, canonical(brief)))
        Store._event(c, "task_queued", {"task": tid, "project": project, "stage": stage, "cycle": cycle})
        return tid

    def enqueue(self, project: str, stage: str, cycle: int, brief: dict,
                next_stage: str | None = None) -> str:
        with self.transaction() as c:
            return self._task(c, project, stage, next_stage, cycle, brief)

    def claim(self, owner: str, lease_seconds: int) -> dict | None:
        now = time.time()
        with self.transaction() as c:
            row = c.execute("""SELECT t.* FROM tasks t JOIN projects p ON t.project_id=p.id
                WHERE p.status='RUNNING' AND (t.status='QUEUED' OR
                 (t.status='RUNNING' AND t.lease_until<?))
                ORDER BY t.rowid LIMIT 1""", (now,)).fetchone()
            if row is None:
                return None
            gen = row["generation"] + 1
            c.execute("""UPDATE tasks SET status='RUNNING', lease_owner=?, lease_until=?,
                generation=? WHERE id=?""", (owner, now + lease_seconds, gen, row["id"]))
            c.execute('INSERT INTO attempt_usage(task,generation,started) VALUES(?,?,?)', (row['id'], gen, now))
            self._event(c, "task_claimed", {"task": row["id"], "owner": owner,
                                            "generation": gen, "reclaimed": row["status"] == "RUNNING"})
            return dict(row) | {"generation": gen, "lease_owner": owner,
                                "brief": json.loads(row["brief"])}

    def assert_lease(self, task: dict) -> None:
        with self.transaction() as c:
            self._assert(c, task)

    @staticmethod
    def _assert(c: sqlite3.Connection, task: dict) -> None:
        row = c.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone()
        if (row is None or row["generation"] != task["generation"] or
            row["lease_owner"] != task["lease_owner"] or row["status"] != "RUNNING" or
                row["lease_until"] <= time.time()):
            raise LeaseLost("lease lost; stale worker may not commit")

    def heartbeat(self, task: dict, seconds: int) -> None:
        with self.transaction() as c:
            self._assert(c, task)
            c.execute("UPDATE tasks SET lease_until=? WHERE id=?", (time.time() + seconds, task["id"]))

    def finish(self, task: dict, status: str, reason: str = "") -> None:
        if status not in ("SUCCEEDED", "FAILED"):
            raise ValueError("invalid terminal task status")
        with self.transaction() as c:
            self._assert(c, task)
            c.execute("""UPDATE tasks SET status=?, failure=?, lease_owner=NULL,
                    lease_until=NULL WHERE id=?""", (status, reason, task["id"]))
            self._event(c, "task_finished", {"task": task["id"], "status": status, "reason": reason})

    def commit(self, task: dict, effect, allow_report=False):
        """Fence filesystem commit against concurrent reclaim using BEGIN IMMEDIATE."""
        with self.transaction() as c:
            self._assert(c, task)
            if not allow_report and self._meter_status(c, task)["budget_exceeded"]:
                from .ledger import BudgetExceeded
                raise BudgetExceeded("circuit_breaker:board_attempt_budget")
            result = effect(c)
            self._assert(c, task)
            return result

    def checkpoint(self, c, task, path, sha256, archive):
        c.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?,?,?)',
                  (task['id'], task['generation'], task['project_id'], path, sha256, archive))
        self._event(c, 'artifact_checkpoint', {'task': task['id'], 'generation': task['generation'],
                    'project': task['project_id'], 'path': path, 'sha256': sha256, 'archive': archive})

    def checkpoints(self, project):
        with self.transaction() as c:
            return [dict(r) for r in c.execute('SELECT * FROM checkpoints WHERE project=? ORDER BY rowid', (project,))]

    @staticmethod
    def _meter_status(c, task):
        from .ledger import DEFAULT_BUDGET
        row = c.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone()
        plan = json.loads(c.execute('SELECT plan FROM projects WHERE id=?', (row['project_id'],)).fetchone()['plan'])
        budget = DEFAULT_BUDGET | plan['stages'][row['stage']].get('budget', {})
        usage = dict(c.execute('SELECT * FROM attempt_usage WHERE task=? AND generation=?', (task['id'], task['generation'])).fetchone())
        wall = time.time() - usage['started']
        exceeded = (usage['fresh'] > budget['max_fresh_tokens'] or usage['calls'] > budget['max_calls'] or wall > budget['max_seconds'])
        stop = exceeded or usage['fresh'] >= budget['max_fresh_tokens'] or usage['calls'] >= budget['max_calls'] or wall >= budget['max_seconds']
        return {'usage': usage, 'stop': stop, 'budget_exceeded': exceeded}

    def meter(self, task, prompt=0, cached=0, completion=0, calls=1):
        if any(type(n) is not int or n < 0 for n in (prompt, cached, completion, calls)) or cached > prompt:
            raise ValueError('invalid usage delta')
        with self.transaction() as c:
            self._assert(c, task)
            c.execute('UPDATE attempt_usage SET calls=calls+?,prompt=prompt+?,cached=cached+?,completion=completion+?,fresh=fresh+? WHERE task=? AND generation=?',
                      (calls,prompt,cached,completion,prompt-cached+completion,task['id'],task['generation']))
            status = self._meter_status(c, task)
            self._event(c, 'attempt_meter', {'task': task['id'], 'generation': task['generation'], **status})
            return status

    def handoff(self, task, body):
        """Accept reports despite budget flags; never accept a stale generation."""
        with self.transaction() as c:
            self._assert(c, task)
            status = self._meter_status(c, task)
            body = dict(body) | {"trusted_meter": status["usage"], "budget_exceeded": status["budget_exceeded"] or bool(body.get("budget_exceeded"))}
            c.execute('INSERT OR REPLACE INTO handoffs VALUES(?,?,?)',
                      (task['id'], task['generation'], canonical(body)))
            kind = 'handoff_error_reported' if body.get('status') == 'error' else 'handoff_accepted'
            self._event(c, kind, {'task': task['id'], 'generation': task['generation'], **body})

    def project(self, project: str) -> dict:
        with self.transaction() as c:
            row = c.execute("SELECT * FROM projects WHERE id=?", (project,)).fetchone()
            if not row:
                raise KeyError(project)
            return dict(row) | {"plan": json.loads(row["plan"])}

    def projects(self) -> list[dict]:
        with self.transaction() as c:
            return [dict(r) for r in c.execute("SELECT id,status,cycle,defect,repeats FROM projects ORDER BY id")]

    def tasks(self, project: str) -> list[dict]:
        with self.transaction() as c:
            return [dict(r) for r in c.execute("SELECT id,stage,cycle,status,failure FROM tasks WHERE project_id=? ORDER BY rowid", (project,))]

    def project_status(self, project: str, status: str, reason: str = "") -> None:
        with self.transaction() as c:
            c.execute("UPDATE projects SET status=? WHERE id=?", (status, project))
            self._event(c, "project_status", {"project": project, "status": status, "reason": reason})

    def failure_cycle(self, project: str, defect: str) -> tuple[int, int]:
        with self.transaction() as c:
            row = c.execute("SELECT cycle,defect,repeats FROM projects WHERE id=?", (project,)).fetchone()
            c.execute("INSERT INTO defect_counts(project,defect,repeats) VALUES(?,?,1) ON CONFLICT(project,defect) DO UPDATE SET repeats=repeats+1", (project,defect))
            n = c.execute("SELECT repeats FROM defect_counts WHERE project=? AND defect=?", (project,defect)).fetchone()["repeats"]
            cycle = row["cycle"] + 1
            c.execute("UPDATE projects SET cycle=?,defect=?,repeats=? WHERE id=?", (cycle, defect, n, project))
            self._event(c, "failed_cycle", {"project": project, "cycle": cycle, "defect": defect, "repeats": n})
            return cycle, n

    def audit(self) -> dict:
        with self.transaction() as c:
            previous = "0" * 64
            count = 0
            for row in c.execute("SELECT * FROM events ORDER BY seq"):
                body = canonical([previous, row["at"], row["kind"], row["payload"]])
                digest = hashlib.sha256(body.encode()).hexdigest()
                if row["previous"] != previous or row["digest"] != digest:
                    return {"valid": False, "first_bad_seq": row["seq"]}
                previous = digest
                count += 1
            return {"valid": True, "events": count, "head": previous}
