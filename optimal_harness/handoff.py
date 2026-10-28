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
