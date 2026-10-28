"""Byte budgets for variable context in driven authoring calls."""
import hashlib
from .store import canonical

LIMIT = 64_000


def pointer(path, value):
    data = (value if isinstance(value, str) else canonical(value)).encode('utf-8')
    return f'{path} ({len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()})'


def bounded(role, render, sources, evidence=None, prior=None, target=None, record=None):
    """Render unchanged first; shed context in order, never the rewrite target."""
    sources, evidence, prior = dict(sources), dict(evidence or {}), dict(prior or {})
    def fail(reason):
        raise RuntimeError(role + '_action_format_exhausted:' + reason)
    if target is not None and len(target[1].encode('utf-8')) > LIMIT:
        fail('target_too_large_for_single_call')
    prompt = render(sources, evidence, prior)
    if len(prompt.encode('utf-8')) <= LIMIT:
        return prompt
    for category, values in (('non_target_files', sources), ('evidence', evidence), ('prior', prior)):
        for path, value in list(values.items()):
            if target is not None and path == target[0]:
                continue
            if category == 'evidence':
                text = value if isinstance(value, str) else canonical(value)
                replacement = text[:4000] + '\n[truncated — full text in ' + path + ']' if len(text) > 4000 else value
            else:
                replacement = pointer(path, value)
            if replacement == value:
                continue
            values[path] = replacement
            if record:
                record({'category': category, 'path': path})
        prompt = render(sources, evidence, prior)
        if len(prompt.encode('utf-8')) <= LIMIT:
            return prompt
    fail('prompt_too_large_for_single_call')


def source_groups(sources):
    """Reports and notes are excerpts; other input paths are file bodies."""
    evidence = {p: v for p, v in sources.items()
                if p == 'cycle_notes' or 'report' in p.lower() or 'handoff' in p.lower()}
    return {p: v for p, v in sources.items() if p not in evidence}, evidence
