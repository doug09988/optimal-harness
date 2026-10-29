# Stage 38 — Semantic anchoring: QA adjudication eligibility + discrepancy anchor inheritance

Branch: `codex/stage-38-semantic-anchoring` from main's tip (cea3ea2).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-38, measured; run stopped by coordinator after
## 4 flat cycles — the tail is structural, not convergent)

Trajectory 6 → 5 → 3 → 2 → 2 → 2 → 2 (7 gates), hidden acceptance
GREEN (0 names) in cycles 4-7 — the artifact is spec-correct. The
stable 2-count tail:
1. reports/qa.json red because QA's `test_case_sensitive` fails —
   the SAME name in QA's file every cycle. Yet Stage 33 adjudication
   never engaged it (all 4 quarantines this run were integration/
   unit-suite tests). Somewhere between the QA report and the
   eligibility tracker, QA failing test NAMES are not registering
   (the QA report's failures surface to the gate as a rollup).
2. One unresolved discrepancy per cycle about that same test —
   each cycle's mint has a DIFFERENT text identity (prose variants:
   "expects 2 occ..." / "expects two e..."), so Stage 35's
   text-identity dedup never matches them; 30 discrepancies were
   minted and 35 dispositions recorded, net zero on this dispute.
   The failed_cycle defect rotates with the identity, so the
   same-defect clock never fires either. One semantic dispute,
   endless surface forms.

## The change

A. **QA eligibility extraction.** Trace how Stage 33's eligibility
   tracker learns failing test names per suite; make the QA
   acceptance report path yield its failing test names (suite +
   test name) to the tracker the same way the integration path
   does, so a QA test failing 2 consecutive gate cycles becomes
   adjudication-eligible. No change to adjudication itself.
B. **Discrepancy anchor inheritance.** When a discrepancy's text
   references a test name (a backtick-quoted `test_...` token —
   extract deterministically, first such token), its ANCHOR is
   that test name. At current-open-set computation (Stage 35):
   if the anchor test is quarantined (Stage 33) OR any earlier
   discrepancy with the same anchor holds a CLOSE disposition in
   the ledger, the new discrepancy is auto-dispositioned with the
   inherited disposition (CLOSE, evidence pointer = the anchor's
   quarantine event / prior closure), recorded as a
   `discrepancy_inherited_closure` event, and never counted.
   Anchored discrepancies with no such history behave as today.
   Unanchored discrepancies are untouched.

## Tests

- A QA-suite test failing two consecutive gate cycles becomes
  adjudication-eligible (tracker-level test using the QA report
  shape produced by the real QA stage).
- A discrepancy anchored to a quarantined test is auto-closed at
  set computation and not counted; a differently-worded mint with
  the same anchor is also auto-closed.
- An anchored discrepancy whose test is NOT quarantined/closed
  stays open and counted.
- Unanchored discrepancies behave exactly as Stage 35.
- Suite fully green (257 existing + new).

## Acceptance

Suite green; coordinator live run: QA's persistent test reaches
adjudication; case-dispute discrepancy mints land pre-closed via
inheritance; trajectory reported against verify-38's 6 → 5 → 3 → 2
tail. A zero-failure gate pass (AWAITING_COORDINATOR) is the goal.

## Stage 38 implementation

- Fixed public receipt failure extraction to accept unittest's optional test
  docstring between the qualified failure name and separator. QA failures now
  reach the same suite/name eligibility tracker as integration failures;
  adjudication verdicts, budgets, and private acceptance checks are unchanged.
- Added deterministic first backtick-quoted `test_...` anchors at Stage 35 set
  computation. Persisted anchor observations retain the link after handoffs are
  superseded. Quarantines or prior CLOSE ledger entries produce durable CLOSE
  dispositions with evidence pointers and `discrepancy_inherited_closure` events.
  Repeated snapshots are idempotent; unanchored and unresolved anchors stay open.
- Added four regression tests covering real QA assembly/report header shape and
  consecutive-cycle eligibility, reworded quarantine inheritance, prior closure
  inheritance after the original disappears, first-anchor selection, project
  isolation, and unchanged unanchored accounting.
- Verification: `python3 -m unittest discover -s tests`
  Final suite result: `Ran 261 tests in 19.061s` / `FAILED (errors=9)`.
  All 252 other tests passed, including the four new tests. The nine errors are
  existing HTTP tests blocked at local socket bind by sandbox
  `PermissionError: [Errno 1] Operation not permitted`. No separate loopback or
  model-server calls were made. A fully green suite remains unverified here.
- Token usage: not reported by the runtime.
- No files staged or committed.
