"""Cross-role proofs over file AND command effects, including directory scopes."""
from __future__ import annotations
from pathlib import Path
from .policy import PolicyError, normalized, confined


def overlaps(a, b):
    def covers(scope, path):
        return scope == path or (scope.endswith('/**') and path.startswith(scope[:-2]))
    return covers(a, b) or covers(b, a)


def prove(plan, root, grader_file):
    immutable = [plan['spec'], *plan.get('immutable_inputs', [])]
    impl, tests = plan.get('implementation_paths', []), plan.get('qa_test_paths', [])
    wiring = plan.get('wiring_paths', [])
    machines = [cmd['capture'] for c in plan['stages'].values() for cmd in c.get('commands', {}).values()]
    private_files = [p for p in grader_file.parent.rglob('*') if p.is_file()]
    if any(overlaps(p, q) for p in machines for q in immutable + impl + tests):
        raise PolicyError("machine receipt overlaps protected or code path")
    for role, c in plan['stages'].items():
        reads = c.get('reads', [])
        writes = c.get('writes', []) + c.get('writable', [])
        command_writes = [p for cmd in c.get('commands', {}).values() for p in cmd.get('writes', [])]
        effects = writes + command_writes
        for p in reads + effects + c.get('command_only', []) + c.get('deliverables', []) + machines:
            normalized(p)
            if p.startswith(('vault/', '.harness/', '.git/')) or p in ('vault', '.harness', '.git'):
                raise PolicyError('reserved runtime namespace')
            full = confined(root, p[:-3] if p.endswith('/**') else p)
            candidates = list(full.rglob('*')) if p.endswith('/**') and full.is_dir() else [full]
            for f in candidates:
                if f.is_symlink():
                    raise PolicyError('symlink plan scope')
                if f.exists() and any(f.samefile(g) for g in private_files):
                    raise PolicyError('leaked-grader plan rejected')
        if any(overlaps(p, q) for p in effects for q in immutable + machines):
            raise PolicyError('immutable input or machine receipt is writable')
        if role != 'QA' and any(overlaps(p, q) for p in effects for q in tests):
            raise PolicyError('only QA writes acceptance tests, including commands')
        if role == 'QA' and any(overlaps(p, q) for p in reads + effects for q in impl):
            raise PolicyError('QA cannot read/write implementation')
        if role == 'INTEGRATOR':
            for p in effects:
                if any(overlaps(p, q) for q in impl) and p not in wiring:
                    raise PolicyError('integrator requires exact declared wiring path')
        if role == 'REVIEW':
            other = [p for r, cfg in plan['stages'].items() if r != 'REVIEW' for p in cfg.get('deliverables', [])]
            if any(overlaps(p, q) for p in effects for q in other):
                raise PolicyError('review cannot mutate prior deliverables')
        for cmd in c.get('commands', {}).values():
            mounts = cmd.get('reads', []) + cmd.get('writes', [])
            if any(p.endswith('/**') for p in mounts):
                raise PolicyError('commands require exact file mounts')
            if any(p not in effects for p in cmd.get('writes', [])):
                raise PolicyError('undeclared command write')
            if type(cmd.get('requires_isolation', False)) is not bool:
                raise PolicyError('requires_isolation must be boolean')
