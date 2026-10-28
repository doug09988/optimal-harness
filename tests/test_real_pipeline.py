"""Scripted model, REAL subprocess suites + hidden oracle + genuine FIXER routing.
This is a harness integration test, NOT evidence of local-model acceptance.
"""
import asyncio
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from optimal_harness.engine import Engine
from optimal_harness.store import Store
from optimal_harness.verifier import LIFECYCLE
from tests.test_harness import FakeQueue

BASE=Path(__file__).resolve().parents[1]
CODE='''import json, sys
from collections import Counter
from pathlib import Path

def main():
    try:
        if len(sys.argv) != 3:
            raise ValueError('expected input and output paths')
        data=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        if not isinstance(data,list) or any(not isinstance(x,str) for x in data):
            raise ValueError('expected JSON array of strings')
        counts=Counter(data)
        result=[{'word':w,'count':counts[w]} for w in sorted(counts)]
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        return 0
    except (OSError,ValueError,TypeError) as exc:
        print(str(exc),file=sys.stderr)
        return 2
if __name__=='__main__':
    sys.exit(main())
'''
UNIT='''import json,subprocess,sys,tempfile,unittest
from pathlib import Path
class Unit(unittest.TestCase):
    def test_counts(self):
        with tempfile.TemporaryDirectory() as d:
            inp,out=Path(d)/'in',Path(d)/'out'
            inp.write_text('["a","a","b"]')
            p=subprocess.run([sys.executable,'wordfreq.py',str(inp),str(out)],capture_output=True)
            self.assertEqual(p.returncode,0)
            self.assertEqual(json.loads(out.read_text()),[{'word':'a','count':2},{'word':'b','count':1}])
'''
QA=UNIT.replace('test_counts','test_empty').replace('["a","a","b"]','[]').replace("[{'word':'a','count':2},{'word':'b','count':1}]",'[]')


class RealPipeline(unittest.TestCase):
    def test_real_hidden_failure_routes_fixer_then_machine_pass(self):
        with tempfile.TemporaryDirectory(dir=BASE/'runs') as tmp:
            base=Path(tmp); root=base/'work'; grader=base/'grader'
            example=BASE/'examples'/'word-counter'
            shutil.copytree(example/'work',root); shutil.copytree(example/'grader',grader)
            plan=json.loads((example/'plan.json').read_text())
            lines=[ln for ln in (root/'spec.md').read_text().splitlines() if ':' in ln]
            audit={'slots':{name:{'status':'citation','quote':line,'because':'Explicit requirement.'} for name,line in zip(LIFECYCLE,lines)}}
            def write(path,data): return {'action':'write','path':path,'data':data}
            def handoff(prompt):
                path=re.search(r'vault/runs/[0-9a-f]{16}/a[0-9]+/handoff.json',prompt).group()
                return write(path,'{"status":"ready","open_discrepancies":[],"summary":"scripted test"}')
            finish={'action':'finish','summary':'scripted test'}
            def review(stage):
                return [write('artifacts/review.json',json.dumps({'reviewed_stage':stage,'counterexamples':['missing input file'],'observations':[],'unresolved':[]})),handoff,finish]
            def integration_and_qa():
                return [write('artifacts/integration_notes.md','Real suite command follows.'),{'action':'command','name':'suite'},handoff,finish,*review('INTEGRATOR'),
                        write('test_acceptance.py',QA),{'action':'command','name':'acceptance'},handoff,finish,*review('QA')]
            def fix(prompt):
                self.assertIn('independent acceptance failed:',prompt)
                return write('wordfreq.py',CODE)
            # Initial implementation intentionally lowercases strings: unit/QA happy
            # paths pass, independently hidden case-sensitive tests genuinely fail.
            wrong=CODE.replace('counts=Counter(data)','counts=Counter(x.lower() for x in data)')
            actions=[write('artifacts/intake.json',json.dumps(audit)),handoff,finish,*review('INTAKE'),
                     write('wordfreq.py',wrong),write('test_unit.py',UNIT),handoff,finish,*review('CODER'),
                     *integration_and_qa(),fix,handoff,finish,*review('FIXER'),*integration_and_qa()]
            store=Store(base/'board.db'); engine=Engine(store,FakeQueue(actions),lease_seconds=120)
            engine.submit(root,plan,grader/'acceptance.json')
            asyncio.run(engine.serve(1))
            self.assertEqual(store.project('wordcounter')['status'],'AWAITING_COORDINATOR', store.tasks('wordcounter'))
            tasks=store.tasks('wordcounter')
            self.assertEqual(sum(t['stage']=='FIXER' for t in tasks),1)
            self.assertEqual(len(tasks),14)
            with store.transaction() as c:
                gates=[json.loads(r['payload']) for r in c.execute("SELECT payload FROM events WHERE kind='satisfiability_gate' ORDER BY seq")]
            self.assertEqual([g['report']['passed'] for g in gates],[False,True])
            self.assertEqual(engine.integrity_errors('wordcounter',root),[])
            self.assertTrue(store.audit()['valid'])
