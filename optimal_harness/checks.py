"""Exact deterministic text/CSV checks (no model judgment)."""
import csv
import io
import json
from .policy import PolicyError

KINDS = {'contains', 'not-contains', 'verbatim', 'csv-count', 'csv-cell', 'csv-set', 'citations-verbatim', 'unchanged'}


def check(v, item):
    kind = item['type']
    if kind == 'unchanged':
        from .verifier import digest
        from .policy import confined
        ok = digest(confined(v.root, item['path'])) == item['expected']
    elif kind in ('contains', 'not-contains'):
        found = item['text'] in v.text(item['path'])
        ok = found if kind == 'contains' else not found
    elif kind == 'verbatim':
        def sliced(path, bounds):
            text = v.text(path)
            lo, hi = bounds
            if type(lo) is not int or (hi is not None and type(hi) is not int) or lo < 0 or (hi is not None and (hi < lo or hi > len(text))):
                raise PolicyError('invalid verbatim slice')
            return text[lo:hi]
        ok = sliced(item['source'], item.get('source_slice', [0, None])) == sliced(item['target'], item.get('target_slice', [0, None]))
    elif kind == 'citations-verbatim':
        data = v.json(item['path'])
        claims = data.get(item.get('claims_key', 'claims'), [])
        ok = bool(claims) and not v.embedded_quotes(item['path'], item['sources'])
        ok = ok and all(isinstance(c, dict) and 'quote' in c for c in claims)
    else:
        rows = list(csv.DictReader(io.StringIO(v.text(item['path']))))
        matching = [r for r in rows if all(r[k] == value for k, value in item.get('where', {}).items())]
        if kind == 'csv-count':
            ok = len(matching) == item['expected']
        elif kind == 'csv-cell':
            ok = len(matching) == 1 and matching[0][item['column']] == str(item['expected'])
        else:
            expected = {r[item['column']] for r in matching}
            actual = v.json(item['target'])
            for key in item.get('target_key', '').split('.'):
                if key:
                    actual = actual[key]
            ok = isinstance(actual, list) and set(actual) == expected and len(actual) == len(set(actual))
    return None if ok else kind + ': deterministic mismatch: ' + item.get('path', item.get('target', ''))
