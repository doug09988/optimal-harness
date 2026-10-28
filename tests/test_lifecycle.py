import json
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from optimal_harness.lifecycle import handle_signal, runner_signals, sweep
from optimal_harness.store import Store


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stale = self.board('stale', time.time() - 31 * 60)
        self.fresh = self.board('fresh', time.time())
        self.terminal = self.board('terminal', time.time() - 60 * 60)
        self.terminal.project_status('project', 'AWAITING_COORDINATOR')
        self.successor = self.board('successor', time.time())

    def board(self, name, at):
        store = Store(self.root / name / 'board.sqlite3')
        with patch('optimal_harness.store.time.time', return_value=at):
            store.add_project('project', self.root, {}, {})
        return store

    def events(self, store):
        with store.transaction() as c:
            return [(r['kind'], json.loads(r['payload'])) for r in c.execute('SELECT * FROM events ORDER BY seq')]

    def test_launch_sweep(self):
        fresh_before = self.events(self.fresh)
        terminal_before = self.events(self.terminal)
        last_at = None
        with self.stale.transaction() as c:
            last_at = c.execute('SELECT at FROM events ORDER BY seq DESC LIMIT 1').fetchone()['at']
        self.assertEqual(sweep(self.root, successor=self.successor), ['stale'])
        self.assertEqual(self.stale.project('project')['status'], 'ABANDONED')
        status, closed = self.events(self.stale)[-2:]
        self.assertEqual(status[0], 'project_status')
        self.assertEqual(status[1]['reason'], 'closed by lifecycle sweep: no event activity for 30+ minutes; successor run successor launched')
        self.assertEqual(closed, ('lifecycle_sweep_closed', {
            'predecessor_run': 'stale', 'last_event_time': last_at, 'successor_run': 'successor'}))
        self.assertEqual(self.events(self.fresh), fresh_before)
        self.assertEqual(self.fresh.project('project')['status'], 'RUNNING')
        self.assertEqual(self.events(self.terminal), terminal_before)
        self.assertEqual(self.events(self.successor)[-1][0], 'lifecycle_sweep_noticed_active')
        self.assertEqual(self.successor.project('project')['status'], 'RUNNING')
        self.assertTrue(self.stale.audit()['valid'])
        self.assertEqual(sweep(self.root, successor=self.successor), [])

    def test_engine_submission_sweeps_siblings(self):
        from optimal_harness.engine import Engine
        example = Path(__file__).resolve().parents[1] / 'examples' / 'word-counter'
        run = self.successor.db.parent
        shutil.copytree(example / 'work', run / 'work')
        shutil.copytree(example / 'grader', run / 'grader')
        plan = json.loads((example / 'plan.json').read_text())
        Engine(self.successor, None).submit(run / 'work', plan, run / 'grader' / 'acceptance.json')
        self.assertEqual(self.stale.project('project')['status'], 'ABANDONED')
        self.assertEqual(self.fresh.project('project')['status'], 'RUNNING')
        self.assertEqual(self.events(self.successor)[-1][0], 'lifecycle_sweep_noticed_active')

    def test_standalone_idempotent(self):
        command = [sys.executable, '-m', 'optimal_harness.lifecycle', '--sweep', str(self.root), '--stale-minutes', '30']
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, 'stale: ABANDONED\n')
        before = self.events(self.stale)
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, '')
        self.assertEqual(self.events(self.stale), before)

    def test_signals(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signum):
                store = self.board(signal.Signals(signum).name, time.time())
                with self.assertRaises(SystemExit) as caught:
                    handle_signal(store, ['project'], signum)
                self.assertEqual(caught.exception.code, 128 + signum)
                self.assertEqual(store.project('project')['status'], 'ABANDONED')
                self.assertEqual(self.events(store)[-1][1]['reason'],
                                 f'runner received {signal.Signals(signum).name}')
                self.assertTrue(store.audit()['valid'])

    def test_signal_preserves_terminal_and_exits_on_write_failure(self):
        before = self.events(self.terminal)
        with self.assertRaises(SystemExit):
            handle_signal(self.terminal, ['project'], signal.SIGTERM)
        self.assertEqual(self.events(self.terminal), before)
        with patch.object(self.fresh, 'transaction', side_effect=RuntimeError('unavailable')):
            with self.assertRaises(SystemExit):
                handle_signal(self.fresh, ['project'], signal.SIGTERM)

    def test_handlers_restored(self):
        before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        with runner_signals(self.fresh, ['project']):
            for s in before:
                self.assertNotEqual(signal.getsignal(s), before[s])
        for s in before:
            self.assertEqual(signal.getsignal(s), before[s])
