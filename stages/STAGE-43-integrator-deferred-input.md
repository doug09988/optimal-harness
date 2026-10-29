# Stage 43 — Deliverable isolation must survive INTEGRATOR packaging

Branch: `codex/stage-43-integrator-deferred-input` from main's tip (4a4d12e).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-42, measured — Stage 42 itself is innocent)

Run died in cycle 1, zero gate evaluations. CODER's test_unit.py
was genuinely syntax-broken in every generation (unterminated
string line 336; then a parenthesis mismatch at line 34) — the
verify-34 failure shape. Stage 34 isolation worked as far as it
went: CODER completed on wordfreq.py alone. But INTEGRATOR then
hard-failed the whole run:
`error:missing_deliverables:CODER package input:test_unit.py`
(integrator.py). The repair seeding Stage 34 created targets the
FIXER, which runs AFTER INTEGRATOR in the stage order — so an
isolated deliverable failure dead-ends before its repair item is
ever reachable. Stage 34's acceptance never exercised the
downstream package contract.

## The change

1. When a CODER deliverable carries a `deliverable_failed` mark
   (Stage 34), INTEGRATOR tolerates its absence from the package:
   assemble without it, record a `package_input_deferred` event
   naming the deferred path and the originating failure, and
   ensure the missing deliverable is present in the cycle's
   FIXER repair seeding (Stage 34's mechanism — wire it if the
   seeding only happens on the CODER stage's own transition).
2. A missing package input WITHOUT a deliverable_failed mark
   stays exactly as fatal as today (no silent tolerance).
3. The gate needs no change: the missing suite surfaces in the
   cycle's reports and counts as it does today — this stage
   removes the hard abort, not the accountability.
4. If the FIXER rebuilds the deferred deliverable in the cycle,
   the NEXT cycle's INTEGRATOR assembles the full package
   normally (no sticky state).

## Tests

- Scripted run shape: CODER writes one deliverable and
  deliverable-fails the other → INTEGRATOR completes with a
  package_input_deferred event → FIXER receives the repair item
  → the cycle reaches a gate evaluation (no run abort).
- Missing package input with no deliverable_failed mark still
  raises the existing fatal error.
- Deferred-then-rebuilt: next cycle INTEGRATOR includes the
  rebuilt deliverable.
- Suite fully green (277 existing + new).

## Acceptance

Suite green; coordinator live run: a CODER deliverable failure
degrades into a fixer repair cycle instead of a run abort;
trajectory reported against verify-42's cycle-1 death.
Zero-failure gate pass (AWAITING_COORDINATOR) remains the goal.

## Stage 43 implementation

- INTEGRATOR reads Stage 34's failed-deliverable marks from the latest
  successful CODER handoff. An absent marked input emits
  `package_input_deferred` with its path, original error, and CODER handoff
  pointer; an absent unmarked input retains the existing fatal error.
- INTEGRATOR omits only those deferred inputs from command read mounts so
  the unchanged suite command runs and records the actual missing-module
  failure rather than aborting during command setup. Gate accounting,
  Stage 34 isolation, and probe machinery are unchanged.
- Existing `_fixer_brief` handoff seeding already carries the failed
  deliverable into the cycle's FIXER; no additional seeding was necessary.
  The scripted pipeline test exercises CODER isolation, INTEGRATOR success,
  a failed gate evaluation, FIXER receiving and rebuilding the missing
  deliverable, and the next INTEGRATOR including it without another
  deferral. A separate test preserves fatal unmarked-missing behavior.
- Verification: `python3 -m unittest discover -s tests`
  Result: `Ran 279 tests in 25.905s` / `FAILED (errors=9)`.
  Both new tests passed (270 tests passed, no assertion failures). The nine
  errors are existing HTTP tests whose local server binds fail with
  `PermissionError: [Errno 1] Operation not permitted` in this sandbox:
  one adversarial API test, one queue test, and seven model tests.
  Full green verification and live coordinator acceptance remain pending
  an environment permitting those existing tests. No separate loopback
  or model-server calls were made.
- Token usage: not reported by the session tooling.
- No files staged or committed.
