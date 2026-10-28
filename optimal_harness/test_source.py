"""Read-only acceptance excerpts for escalated FIXER briefs."""
import ast
import json
import re

from .policy import confined, PolicyError
from .prompt_budget import LIMIT

BANNER = ('FAILING TEST SOURCE — read-only. These tests define the expected\n'
          'behavior. Conform the implementation to them. Do not modify them.')


def extract(plan, root, failures):
    details = '\n'.join(failures)
    names = list(dict.fromkeys(re.findall(r'\btest_[A-Za-z0-9_]+\b', details)))
    paths = sorted(set(plan.get('qa_test_paths', [])) | {
        p for p in plan['stages']['QA'].get('deliverables', [])
        if p.endswith('test_acceptance.py')})
    bodies = {}
    for path in paths:
        try:
            text = confined(root, path).read_text(encoding='utf-8')
            tree = ast.parse(text)
        except (OSError, UnicodeError, SyntaxError, PolicyError):
            continue
        lines = text.splitlines(keepends=True)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                body = ''.join(lines[start - 1:node.end_lineno]).rstrip('\n')
                if len(body) > 2000:
                    body = body[:2000] + '\n[truncated — full text in ' + path + ']'
                bodies.setdefault(node.name, (path, body))
    return [dict(name=name, path=bodies.get(name, (paths[0] if paths else 'test_acceptance.py',))[0],
                 source=bodies.get(name, (None, None))[1],
                 details='\n'.join(line for line in details.splitlines() if name in line))
            for name in names]


def section(tests):
    return BANNER + '\n\n' + '\n\n'.join(
        t['name'] + ' — ' + t['path'] + '\n' +
        (t['source'] if t.get('source') is not None else
         ('(source unavailable)' if not t.get('pointer') else '(see file)')) +
        ('\n' + t['details'] if t.get('details') else '') for t in tests)


def shed(tests, render):
    """Keep failure order as priority; drop the last body first."""
    tests = [dict(t) for t in tests]
    value = render(tests)
    for test in reversed(tests):
        if len(value.encode('utf-8')) <= LIMIT:
            break
        test.update(source=None, pointer=True, details='')
        value = render(tests)
    return value, tests


def attach(brief, plan, root, failures):
    tests = extract(plan, root, failures)
    def fields(items):
        return dict(failing_tests=items, failing_test_source=section(items),
                    test_source_evidence=dict(included=True, attached_tests=[
                        t['name'] for t in items if t.get('source') is not None]))
    _, tests = shed(tests, lambda items: json.dumps(
        dict(brief, **fields(items)), ensure_ascii=False))
    brief.update(fields(tests))
