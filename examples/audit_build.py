"""Read-only run/chain audit and build provenance, writing only into repo runs/."""
from __future__ import annotations
import difflib
import hashlib
import json
from pathlib import Path
import sqlite3
from optimal_harness.policy import confined
from optimal_harness.store import canonical
from optimal_harness.verifier import digest

BASE=Path(__file__).resolve().parents[1]


def run_summary(directory):
    db=directory/'board.sqlite3'
    c=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True); c.row_factory=sqlite3.Row
    projects=[dict(r) for r in c.execute('SELECT id,status,root,plan FROM projects')]
    tasks=[dict(r) for r in c.execute('SELECT id,stage,status,cycle,failure FROM tasks ORDER BY rowid')]
    events=[dict(r) for r in c.execute('SELECT * FROM events ORDER BY seq')]
    started=sum(e['kind']=='model_call_started' for e in events)
    usage=[json.loads(e['payload']) for e in events if e['kind']=='model_call_finished']
    totals={k:sum(u[k] for u in usage) for k in ('prompt_tokens','cached_tokens','completion_tokens','fresh_tokens')}
    previous='0'*64; chain=True
    for e in events:
        expected=hashlib.sha256(canonical([previous,e['at'],e['kind'],e['payload']]).encode()).hexdigest()
        if e['previous'] != previous or e['digest'] != expected: chain=False
        previous=e['digest']
    pins=[]; integrity=[]
    for project in projects:
        plan=json.loads(project.pop('plan')); root=Path(project.pop('root'))
        for key in ('input_hashes','charter_hashes','grading_hashes'):
            for path,expected in plan.get(key,{}).items():
                f=Path(path) if key=='grading_hashes' else confined(root,path)
                try:
                    if digest(f)!=expected: pins.append(key+':'+path)
                except OSError: pins.append('missing:'+path)
        latest={}
        for cp in c.execute('SELECT * FROM checkpoints WHERE project=? ORDER BY rowid',(project['id'],)):
            try:
                if digest(confined(root,cp['archive']))!=cp['sha256']: integrity.append('archive:'+cp['archive'])
            except OSError: integrity.append('missing archive:'+cp['archive'])
            latest[cp['path']]=cp['sha256']
        for path,expected in latest.items():
            try:
                if digest(confined(root,path))!=expected: integrity.append('current differs:'+path)
            except OSError: integrity.append('missing current:'+path)
    c.close()
    return {'run':directory.name,'projects':projects,'tasks':tasks,'calls_started':started,
            'calls_with_server_usage':len(usage),'unmetered_started_calls':started-len(usage),
            'known_usage_only':totals,'event_chain_valid':chain,'event_count':len(events),
            'pin_errors':pins,'checkpoint_errors':integrity,
            'result_file_exists':(directory/'result.json').exists()}


def main():
    runs=BASE/'runs'
    summaries=[run_summary(d) for d in sorted(runs.glob('wordcounter-*')) if d.is_dir() and (d/'board.sqlite3').exists()]
    (runs/'example-audit.json').write_text(json.dumps(summaries,indent=2)+'\n')
    package_files=sorted((BASE/'optimal_harness').rglob('*.py'))+sorted((BASE/'optimal_harness'/'charters').glob('*.md'))
    tests=sorted((BASE/'tests').glob('*.py'))
    provenance={str(p.relative_to(BASE)):{'sha256':digest(p),'bytes':p.stat().st_size} for p in package_files+tests+[BASE/'SPEC.md']}
    (runs/'source-manifest.json').write_text(json.dumps(provenance,indent=2)+'\n')
    ref=BASE/'references'/'reliable-harness'/'reliable_harness'
    changes=[]
    own=BASE/'optimal_harness'
    for name in sorted({str(p.relative_to(own)) for p in own.rglob('*') if p.is_file() and '__pycache__' not in str(p)} | {str(p.relative_to(ref)) for p in ref.rglob('*') if p.is_file() and '__pycache__' not in str(p)}):
        before=(ref/name).read_text() if (ref/name).is_file() else ''
        after=(own/name).read_text() if (own/name).is_file() else ''
        changes.extend(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='reference/'+name,tofile='optimal_harness/'+name))
    (runs/'reference-diff.patch').write_text(''.join(changes))
    print(json.dumps({'package_source_files':len(package_files),'test_source_files':len(tests),
                      'runs':len(summaries),'audit_path':str(runs/'example-audit.json'),
                      'source_manifest':str(runs/'source-manifest.json'),'diff':str(runs/'reference-diff.patch')},indent=2))


if __name__=='__main__': main()
