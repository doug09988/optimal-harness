"""Deterministic spec obligations; no model interpretation or inferred completion."""
import hashlib
import re

REFERENCE = re.compile(r'\b(Appendix\s+[A-Za-z]|Section\s+\d+(?:\.\d+)*|Figure\s+\d+|Table\s+\d+)\b', re.I)


def _item(kind, label):
    return {'id': kind + '-' + hashlib.sha256(label.casefold().encode()).hexdigest()[:16],
            'kind': kind, 'label': label}


def extract(spec):
    anchors = set()
    for line in spec.splitlines():
        # Headings, explicit HTML ids, and numbered captions/section anchors.
        heading = re.match(r'^\s*(?:#{1,6}\s*)?(Appendix\s+[A-Za-z]|Section\s+\d+(?:\.\d+)*|Figure\s+\d+|Table\s+\d+)\b(.*)$', line, re.I)
        if heading and (not heading[2].strip() or re.match(r'^\s*[:.\-–—]', heading[2]) or line.lstrip().startswith('#')):
            anchors.add(' '.join(heading[1].casefold().split()))
        for anchor in re.findall(r'(?:id|name)=["\']([^"\']+)["\']', line, re.I):
            anchors.add(re.sub(r'[-_]', ' ', anchor).casefold())
    result = {}
    for match in REFERENCE.finditer(spec):
        label = ' '.join(match[0].split())
        if label.casefold() not in anchors:
            item = _item('missing_reference', label)
            result[item['id']] = item
    # A duty requires both whole-word predicates in the SAME sentence.
    for sentence in re.split(r'(?<=[.!?])\s+|\n\s*\n', spec):
        sentence = ' '.join(sentence.split())
        if (re.search(r'\b(?:record|report|flag)\b', sentence, re.I) and
                re.search(r'\b(?:handoff|open_discrepancies)\b', sentence, re.I)):
            item = _item('duty', sentence)
            result[item['id']] = item
    return sorted(result.values(), key=lambda item: item['id'])


def reported(item, text):
    return isinstance(text, str) and (item['label'].casefold() in text.casefold() or item['id'] in text)


def acknowledged_discrepancy(text, items):
    return any(reported(item, text) for item in items)


def errors(items, handoffs):
    """handoffs are (role, canonical archived document) pairs from this cycle."""
    result = []
    for item in items:
        addressed = False
        for role, doc in handoffs:
            discrepancies = doc.get('open_discrepancies', [])
            if not isinstance(discrepancies, list):
                continue
            if item['kind'] == 'missing_reference':
                addressed |= role == 'CODER' and any(reported(item, text) for text in discrepancies)
            else:
                addressed |= any(reported(item, text) for text in discrepancies)
                for resolution in doc.get('obligation_resolutions', []):
                    if isinstance(resolution, dict) and resolution.get('id') == item['id']:
                        addressed |= bool(resolution.get('evidence')) or resolution.get('discrepancy') in discrepancies
        if not addressed:
            result.append('obligation_unaddressed:' + item['id'])
    return result


def resolutions(value, items, discrepancies, source_read):
    """Validate explicit duty acknowledgments, never infer them from a summary."""
    if not isinstance(value, list):
        raise ValueError('finish.obligation_resolutions must be a list')
    known = {item['id']: item for item in items}
    result = []
    seen = set()
    for entry in value:
        if not isinstance(entry, dict) or entry.get('id') not in known or entry['id'] in seen:
            raise ValueError('obligation resolution requires a unique known id')
        seen.add(entry['id'])
        if 'discrepancy' in entry:
            if not isinstance(entry['discrepancy'], str) or entry['discrepancy'] not in discrepancies:
                raise ValueError('obligation discrepancy must appear in open_discrepancies')
            result.append({'id': entry['id'], 'discrepancy': entry['discrepancy']})
        else:
            evidence = entry.get('evidence')
            if not isinstance(evidence, dict) or any(not isinstance(evidence.get(k), str) or not evidence[k].strip()
                                                    for k in ('source', 'quote', 'because')):
                raise ValueError('obligation evidence requires source, quote, because')
            if evidence['quote'] not in source_read(evidence['source']):
                raise ValueError('obligation evidence quote is not verbatim')
            result.append({'id': entry['id'], 'evidence': {k: evidence[k] for k in ('source', 'quote', 'because')}})
    return result
