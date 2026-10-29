# Stage 44 — FIXER write allowlist covers the cycle's repair items

Branch: `codex/stage-44-fixer-allowlist-repair-items` from main's tip (70581d9).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-43, measured)

Stage 43 worked live: CODER deliverable-failed test_unit.py,
INTEGRATOR deferred it (package_input_deferred), the cycle
reached its gate (6). Then the cycle-1 FIXER stage FAILED the
whole run: `fix target not on declared write allowlist:
test_unit.py`. The repair chain has three links — isolation
marks it (Stage 34), INTEGRATOR defers it (Stage 43), the FIXER
is seeded with it — and the third link is policy-blocked: the
FIXER's declared write allowlist covers the project's
implementation paths, not a CODER test deliverable routed to it
as a repair item. (The FIXER's attempted writes were also
syntax-rejected twice, lines 36/49 — the model's known struggle
with this file — but the stage-killing reason is the allowlist.)

## The change

1. For each cycle, the FIXER's write allowlist is extended with
   exactly the paths of that cycle's repair items: deliverables
   carrying a deliverable_failed mark or a package_input_deferred
   deferral that the FIXER brief includes as repair targets.
   Nothing else is added; files not in the brief stay unwritable
   (the denial path and its event are unchanged).
2. The extension is per-cycle and derived from the brief's own
   repair items — no project-definition change, no persistent
   widening. Record a `fixer_allowlist_extended` event listing
   the added paths for the cycle.
3. Repair writes go through the existing write path (fence
   repair, byte-compile validation, rejection reasons) exactly
   like any other FIXER write.
4. Nothing else changes: if the FIXER still cannot write the
   file within its generations, the stage completes per Stage 34
   semantics (deliverable stays failed, gate counts the missing
   suite) rather than aborting the run on the allowlist reason.

## Tests

- Scripted cycle: a deliverable_failed CODER deliverable routed
  to the FIXER is writable by it (allowlist extension event
  recorded); the FIXER stage completes; the run reaches a gate.
- A path NOT among the cycle's repair items is still denied to
  the FIXER (existing denial tests keep passing).
- The extension does not leak into the next cycle's allowlist
  when that cycle has no repair items.
- Suite fully green (279 existing + new).

## Acceptance

Suite green; coordinator live run: the deferred-repair chain
completes end-to-end (mark → defer → fixer write attempt under
an extended allowlist); trajectory reported against verify-43's
cycle-1 FIXER death. Zero-failure gate pass
(AWAITING_COORDINATOR) remains the goal.

## Stage 44 implementation result

- Extended only the task-local FIXER allowlist and write broker with repair paths
  from that cycle's brief `requested_changes`, excluding protected QA paths and
  paths already covered by the declared allowlist. Project definitions remain
  unchanged; QA/review artifact findings do not grant write permission.
- Added `fixer_allowlist_extended` with task, generation, cycle, and sorted added
  paths. Existing write validation, denial, gate accounting, and Stage 34/43
  behavior are unchanged.
- Removed the deferred-input integration fixture's explicit write permission;
  it now verifies mark → defer → extended FIXER write → subsequent passing gate.
  Added tests for denial outside the brief and no extension leakage into a next
  cycle without repair items, including syntax rejection and fence repair on
  the extended target.
- Verification: `python3 -m unittest discover -s tests`
  Result: `Ran 281 tests in 26.141s` / `FAILED (errors=9)`.
  The other 272 tests passed, including all Stage 44 coverage. All nine errors
  were `PermissionError: [Errno 1] Operation not permitted` when existing HTTP
  tests attempted local server binds (test_adversarial, test_harness, and seven
  test_model tests). No separate loopback/model-server calls were made.
- Coordinator live acceptance remains pending; no live-run claim is made.
- Token usage: not reported by this session.
- No files staged or committed.
