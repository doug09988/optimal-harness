"""Fingerprint only checker failures, excluding command-run nondeterminism.

Fixer notes remain VERBATIM. Only the hash input removes ephemeral cwd paths,
unittest timings and memory addresses. It never includes model commentary.
"""
import hashlib
import re
from .store import canonical


def fingerprint(failures, root):
    normalized = []
    for failure in failures:
        text = failure.replace(str(root), '/job')
        text = re.sub(r'/job/\.harness/commands/cmd-[^/\s]+', '/job', text)
        text = re.sub(r'Ran (\d+) tests? in [\d.]+s', r'Ran \1 tests in <time>s', text)
        text = re.sub(r'0x[0-9a-fA-F]+', '<address>', text)
        normalized.append(text)
    return hashlib.sha256(canonical(normalized).encode()).hexdigest()[:16]
