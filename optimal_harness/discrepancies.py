"""Runtime-owned discrepancy identities, dispositions, and current-set accounting."""
import ast
import hashlib
import json
import re
from .policy import confined


def identity(text):
    return hashlib.sha256(' '.join(text.casefold().split()).encode()).hexdigest()


def current(review, handoffs):
    texts = list(review.get('unresolved', []))
    for doc in handoffs:
        texts.extend(doc.get('open_discrepancies', []))
    return {identity(text): text for text in texts if isinstance(text, str) and text.strip()}


def validated(entries, known, role='FIXER'):
    accepted = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        key, disposition, note = (entry.get(k) for k in ('identity', 'disposition', 'note'))
        if (isinstance(key, str) and key in known and isinstance(disposition, str) and disposition in ({'STALE'} if role == 'REVIEW' else {'CLOSE', 'KEEP'})
                and isinstance(note, str) and note.strip()):
            accepted.append(dict(identity=key, disposition=disposition, note=note.strip()))
    return accepted


def record(store, project, entries, task=None):
    def effect(c):
        for entry in entries:
            previous = c.execute('SELECT body FROM discrepancy_resolutions WHERE project=? AND identity=?',
                                 (project, entry['identity'])).fetchone()
            if previous and json.loads(previous[0]) == entry:
                continue
            store._event(c, 'discrepancy_resolution', dict(project=project, **entry))
            c.execute('INSERT OR REPLACE INTO discrepancy_resolutions VALUES(?,?,?)',
                      (project, entry['identity'], json.dumps(entry)))
    if task:
        store.commit(task, effect)
    else:
        with store.transaction() as c:
            effect(c)


def snapshot(store, project, root, plan):
    with store.transaction() as c:
        rows = c.execute("SELECT h.body,t.stage FROM handoffs h JOIN tasks t ON t.id=h.task "
                         "WHERE t.project_id=? AND t.status='SUCCEEDED' "
                         "ORDER BY t.rowid DESC,h.generation DESC", (project,)).fetchall()
        ledger = {r[0]: json.loads(r[1]) for r in c.execute(
            'SELECT identity,body FROM discrepancy_resolutions WHERE project=?', (project,))}
    latest = {}
    for row in rows:
        if row['stage'] in latest:
            continue
        doc = json.loads(row['body'])
        if doc.get('handoff'):
            doc = json.loads(confined(root, doc['handoff']).read_text())
        latest[row['stage']] = doc
    review_path = confined(root, 'artifacts/review.json')
    review = json.loads(review_path.read_text()) if review_path.is_file() else {}
    opened = current(review, latest.values())
    cycle = store.project(project)['cycle']
    cycle_tasks = {t['id'] for t in store.tasks(project)
                   if t['cycle'] == cycle and t['status'] == 'SUCCEEDED'}
    generated = {cp['path'] for cp in store.checkpoints(project) if cp['task'] in cycle_tasks}
    names = {}
    for path in sorted(set(plan.get('qa_test_paths', [])) | set(plan.get('unit_test_paths', [])) | {p for p in plan.get('implementation_paths', []) if p.rsplit('/', 1)[-1].startswith('test_')}):
        if path not in generated:
            continue
        try:
            tree = ast.parse(confined(root, path).read_text())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'):
                names[node.name.casefold()] = path
    stale = []
    for key, text in opened.items():
        match = re.search(r'\b(test_\w+)\s*:\s*unwritable\b', text, re.I)
        if match and match[1].casefold() in names:
            stale.append(dict(identity=key, disposition='STALE', note=names[match[1].casefold()] + ':' + match[1]))
    record(store, project, stale)
    ledger.update({e['identity']: e for e in stale})
    return [dict(identity=key, text=text, **{k: v for k, v in ledger.get(key, {}).items() if k != 'identity'})
            for key, text in sorted(opened.items())]


def live(items):
    return [item for item in items if item.get('disposition') not in ('CLOSE', 'STALE')]


def resolutions(store, project):
    with store.transaction() as c:
        return [json.loads(r[0]) for r in c.execute(
            'SELECT body FROM discrepancy_resolutions WHERE project=? ORDER BY identity', (project,))]
