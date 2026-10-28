"""Close inactive run boards and record best-effort runner signal exits."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
import signal
import sqlite3
import time

from .store import Store


def sweep(runs_root: Path, *, successor: Store | None = None,
          stale_minutes: float = 30) -> list[str]:
    """Atomically inspect each sibling board; never rewrite terminal projects."""
    if stale_minutes < 0:
        raise ValueError('stale minutes must be nonnegative')
    successor_name = successor.db.parent.name if successor else None
    closed = []
    for board in sorted(Path(runs_root).glob('*/board.sqlite3')):
        if successor and board.resolve() == successor.db:
            continue
        with sqlite3.connect(board, timeout=30) as c:
            c.row_factory = sqlite3.Row
            c.execute('BEGIN IMMEDIATE')
            projects = list(c.execute("SELECT id FROM projects WHERE status='RUNNING'"))
            if not projects:
                continue
            last = c.execute('SELECT at FROM events ORDER BY seq DESC LIMIT 1').fetchone()
            # Real boards have submission events; without a timestamp there is
            # no evidence that the inactivity threshold has elapsed.
            if last is None:
                continue
            payload = {'predecessor_run': board.parent.name,
                       'last_event_time': last['at'], 'successor_run': successor_name}
            if time.time() - last['at'] < stale_minutes * 60:
                if successor:
                    successor.event('lifecycle_sweep_noticed_active', payload)
                continue
            reason = f'closed by lifecycle sweep: no event activity for {stale_minutes:g}+ minutes'
            reason += (f'; successor run {successor_name} launched' if successor_name
                       else '; standalone sweep')
            for project in projects:
                c.execute("UPDATE projects SET status='ABANDONED' WHERE id=?", (project['id'],))
                Store._event(c, 'project_status', {'project': project['id'],
                             'status': 'ABANDONED', 'reason': reason})
            Store._event(c, 'lifecycle_sweep_closed', payload)
            closed.append(board.parent.name)
    return closed


def handle_signal(store: Store, projects: list[str], signum: int, frame=None) -> None:
    """Best effort only: the next launch's sweep is the durable fallback."""
    try:
        with store.transaction() as c:
            for project in projects:
                row = c.execute('SELECT status FROM projects WHERE id=?', (project,)).fetchone()
                if row and row['status'] == 'RUNNING':
                    c.execute("UPDATE projects SET status='ABANDONED' WHERE id=?", (project,))
                    Store._event(c, 'project_status', {'project': project,
                                 'status': 'ABANDONED',
                                 'reason': f'runner received {signal.Signals(signum).name}'})
    except Exception:
        pass
    raise SystemExit(128 + signum)


@contextmanager
def runner_signals(store: Store, projects: list[str]):
    previous = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(
                signum, lambda number, frame: handle_signal(store, projects, number, frame))
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sweep', required=True, type=Path, metavar='RUNS_ROOT')
    parser.add_argument('--stale-minutes', type=float, default=30)
    args = parser.parse_args(argv)
    if args.stale_minutes < 0:
        parser.error('--stale-minutes must be nonnegative')
    for name in sweep(args.sweep, stale_minutes=args.stale_minutes):
        print(f'{name}: ABANDONED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
