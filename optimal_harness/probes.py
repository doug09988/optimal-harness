"""Harness-owned CLI probes. Declarations and scratch files never enter briefs."""
import json
from pathlib import Path
import sys
import tempfile
from .policy import confined, sandbox_command, PolicyError


def run_probes(root, plan, ledger, store, task):
    # Project CLI convention: first implementation path takes INPUT.json OUTPUT.json.
    failures = []
    for probe in plan.get('probes', []):
        expected = {'exit_code': probe['expected_exit']}
        if 'expected_output' in probe:
            expected['output'] = probe['expected_output']
        actual = {}
        try:
            with tempfile.TemporaryDirectory(prefix='golden-probe-') as tmp:
                scratch = Path(tmp)
                paths = plan['implementation_paths']
                for path in paths:
                    source = confined(root, path)
                    if source.is_file():
                        dest = confined(scratch, path)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(source.read_bytes())
                (scratch / 'probe-input.json').write_text(json.dumps(probe['input']), encoding='utf-8')
                (scratch / 'probe-output.json').write_text('', encoding='utf-8')
                command = {'argv': [sys.executable, paths[0], 'probe-input.json', 'probe-output.json'],
                           'reads': [p for p in paths if confined(scratch, p).is_file()] + ['probe-input.json'],
                           'writes': ['probe-output.json']}
                result = sandbox_command(scratch, command, min(ledger.budget['max_command_seconds'],
                                         ledger.budget['max_seconds'] - ledger.seconds))
                actual['exit_code'] = result['exit_code']
                if 'expected_output' in probe:
                    try:
                        actual['output'] = json.loads((scratch / 'probe-output.json').read_text(encoding='utf-8'))
                    except (ValueError, OSError) as exc:
                        actual['output_error'] = str(exc)[:800]
        except (OSError, ValueError, PolicyError) as exc:
            actual['error'] = str(exc)[:800]
        # JSON encoding distinguishes booleans from numbers while ignoring object key order.
        if json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
            failure = {'name': probe['name'], 'expected': expected, 'actual': actual}
            failures.append(failure)
            store.event('probe_failed', dict(failure, task=task['id'], project=task['project_id'],
                                            cycle=task['cycle'], stage=task['stage']))
    return failures
