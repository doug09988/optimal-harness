"""Fingerprint only checker failures, excluding command-run nondeterminism.

Fixer notes remain VERBATIM. The hash uses failing unittest identifiers when
available, otherwise normalized failure strings. It never includes model commentary.
"""
import hashlib
import re
from .store import canonical


_FAILING_TEST = re.compile(
    r'\b(?:FAIL|ERROR):\s+(?P<header>test\w*\s+\([^()\s]+\))'
    r'|\b(?P<result>test\w*\s+\([^()\s]+\))\s+\.\.\.\s+(?:FAIL|ERROR)\b'
)


def fingerprint(failures, root):
    normalized = []
    names = set()
    for failure in failures:
        for match in _FAILING_TEST.finditer(failure):
            names.add(' '.join((match.group('header') or match.group('result')).split()))
        text = failure.replace(str(root), '/job')
        text = re.sub(r'/job/\.harness/commands/cmd-[^/\s]+', '/job', text)
        text = re.sub(r'Ran (\d+) tests? in [\d.]+s', r'Ran \1 tests in <time>s', text)
        text = re.sub(r'0x[0-9a-fA-F]+', '<address>', text)
        normalized.append(text)
    return hashlib.sha256(canonical(sorted(names) if names else normalized).encode()).hexdigest()[:16]
