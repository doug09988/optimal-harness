"""Handoff shape shared by worker feedback and the stage readiness gate."""
import json


def ready_document(value):
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except ValueError as exc:
        raise ValueError('handoff JSON must be a complete object') from exc
    if not isinstance(data, dict):
        raise ValueError('handoff must be an object')
    if data.get('status') != 'ready':
        raise ValueError('handoff.status must equal ready')
    if not isinstance(data.get('open_discrepancies'), list):
        raise ValueError('handoff.open_discrepancies must be a list; use [] only when none remain')
    return data


def compose_handoff(action, deliverables, obligation_resolutions=None):
    """Runtime-owned envelope; worker supplies only factual content."""
    if not isinstance(action.get('summary'), str) or not action['summary'].strip():
        raise ValueError('finish.summary must be a nonempty string')
    if not isinstance(action.get('open_discrepancies'), list) or any(
            not isinstance(item, str) or not item.strip() for item in action['open_discrepancies']):
        raise ValueError('finish.open_discrepancies must be a list of nonempty strings; use [] only when none remain')
    return ready_document({'status': 'ready', 'summary': action['summary'],
                           'open_discrepancies': action['open_discrepancies'],
                           'deliverables': list(deliverables),
                           'obligation_resolutions': obligation_resolutions or []})
