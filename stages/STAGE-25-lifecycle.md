# Stage 25 — Run lifecycle hygiene: no board left RUNNING forever

Branch: `codex/stage-25-lifecycle` from the tip of `codex/stage-24-fixer-resilience`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence (2026-10-28, from the campaign boards)

verify-24's board still reads RUNNING today even though verify-25 and
verify-26 launched and finished after it. The coordinator stopped that run
externally (process kill) mid-QA to build Stage 23; the runner never wrote a
terminal status, so the project row sits open forever. A board stuck at
RUNNING corrupts campaign accounting — completion/escalation rates per run
are methodology data, not bookkeeping trivia. verify-25 and verify-26 both
reached honest terminal states; only externally-killed runs leak.

## The change

1. **Launch-time sweep:** when a run starts (the run_local entry path and
   the engine's project creation — pick the one place every launch passes
   through), scan the sibling run boards under the same runs root
   (runs/*/board.sqlite3 relative to the new run's directory). For every
   OTHER board whose project status is RUNNING:
   - if its board's latest event is older than 30 minutes, close it:
     set the project status to ABANDONED and append a `project_status`
     event (the existing event kind) with reason
     "closed by lifecycle sweep: no event activity for 30+ minutes;
     successor run <name> launched" — and a dedicated event kind
     `lifecycle_sweep_closed` carrying the predecessor run name, its last
     event time, and the successor run name.
   - if its latest event is fresh (<30 min), leave it alone (a genuinely
     concurrent run is not this stage's business) but record a
     `lifecycle_sweep_noticed_active` event in the NEW run's board.
2. **Standalone invocation:** the sweep must be runnable without starting
   a run — `python3 -m optimal_harness.lifecycle --sweep <runs-root>
   [--stale-minutes 30]` — so operations can close stale boards directly.
   It prints one line per closed board.
3. **Self-closure on signals:** the runner's entry path installs a
   SIGTERM/SIGINT handler that best-effort writes project status ABANDONED
   with reason "runner received <signal>" before exiting. (The sweep is
   the guarantee; the handler is the fast path. Keep it small.)
4. The status vocabulary: use ABANDONED (add it wherever statuses are
   enumerated/validated in the codebase, if anywhere). Do not change the
   meaning of RUNNING/AWAITING_COORDINATOR/ESCALATED/SUCCEEDED anywhere.

## Tests

- Fixture: two fake sibling boards, one RUNNING with a last event 31+
  minutes old, one RUNNING fresh. Sweep closes only the stale one, with
  both events recorded and the reason string present; the fresh board is
  untouched.
- Standalone module invocation on the fixture root prints the closed
  board and exits 0; a second invocation is a no-op (idempotent).
- A board already in a terminal state is never rewritten.
- SIGTERM handler: unit-test the handler function directly (writes
  ABANDONED + reason for the current project) without sending real
  signals to the test process.
- Suite fully green.

## Acceptance

Suite green; the coordinator will run the standalone sweep against the
real runs/ root — success = verify-24's board closes as ABANDONED with the
reason recorded, and verify-25/26 boards are untouched.

## Stage 25 implementation

- Added `optimal_harness.lifecycle`: transactional inactivity sweep, atomic
  ABANDONED/project_status and lifecycle_sweep_closed events, fresh-run notices,
  and the standalone `--sweep` / `--stale-minutes` command with idempotent output.
- Engine project submission sweeps siblings for the specified `board.sqlite3`
  run-board layout. Both runner entry paths install and restore SIGTERM/SIGINT
  handlers that best-effort abandon RUNNING projects and exit with signal status.
  Project statuses have no enumerated validator requiring a vocabulary change.
  Stage flows, gates, and budgets are unchanged.
- Added six tests covering stale/fresh/terminal boards, audit-chain validity,
  engine submission, standalone invocation/idempotency, direct signal handling,
  write failure, and handler restoration.
- Verification: `python3 -m unittest discover -s tests`.
  Suite result: `Ran 197 tests in 14.337s` / `FAILED (errors=9)`.
  All six lifecycle tests passed. All nine errors are existing HTTP fixtures
  denied loopback socket binds by the sandbox (`PermissionError: [Errno 1]
  Operation not permitted`); no live/model-server verification was attempted.
- Verification side effect: before restricting the launch hook to the specified
  `board.sqlite3` layout, existing tests submitted temporary `board.db` boards
  under the real runs root. That first suite run swept `verify-24` and
  `tonight-gate` to ABANDONED, with successor `tmpqpbvm7yo` recorded in their
  lifecycle events. Those append-only audit records were preserved. Subsequent
  suite runs use the restricted hook and do not sweep from those fixtures.
- No files staged or committed. Token usage was not reported by the runtime.
