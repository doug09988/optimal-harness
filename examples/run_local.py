"""Reproducible isolated local-model example driver; never grades its own result."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import shutil
import sys
from optimal_harness.engine import Engine
from optimal_harness.model import LocalChatClient, ModelQueue
from optimal_harness.store import Store


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--name', default='word-counter')
    p.add_argument('--run-dir', required=True)
    p.add_argument('--endpoint', default='http://127.0.0.1:8080/v1')
    p.add_argument('--model', default='mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit')
    args = p.parse_args()
    base = Path(__file__).resolve().parents[1]
    target = Path(args.run_dir).resolve()
    if not target.is_relative_to(base / 'runs') or target.exists():
        raise ValueError('run-dir must be new and inside repository runs/')
    example = base / 'examples' / args.name
    target.mkdir(parents=True)
    shutil.copytree(example / 'work', target / 'work')
    shutil.copytree(example / 'grader', target / 'grader')
    plan = json.loads((example / 'plan.json').read_text())
    (target / 'submitted-plan.json').write_text(json.dumps(plan, indent=2)+'\n')
    store = Store(target / 'board.sqlite3')
    client = LocalChatClient(args.endpoint, args.model)
    queue = ModelQueue(client, base / 'runs' / 'model.lock')
    engine = Engine(store, queue, lease_seconds=1800)
    project = engine.submit(target / 'work', plan, target / 'grader' / 'acceptance.json')
    asyncio.run(engine.serve(jobs=1))
    result = {'project': store.projects(), 'tasks': store.tasks(project), 'audit': store.audit(),
              'integrity_errors': engine.integrity_errors(project, target / 'work')}
    (target / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2), flush=True)
    return 0 if store.project(project)['status'] == 'AWAITING_COORDINATOR' else 1


if __name__ == '__main__':
    sys.exit(main())
