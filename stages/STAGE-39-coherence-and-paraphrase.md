# Stage 39 — Adjudication coherence gate + paraphrase-tolerant discrepancy identity

Branch: `codex/stage-39-coherence-and-paraphrase` from main's tip (621d12b).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-39, measured; stopped after 3 flat cycles at 1)

Trajectory 6 → 6 → 4 → 3 → 2 → 1 → 1 → 1 (8 gates), hidden
acceptance GREEN all 8 cycles, 4 quarantines, 5 inherited
closures, 23 discrepancy resolutions. Two residual defects:

A. **Incoherent adjudication verdicts.** Cycle 4 produced two
   ARTIFACT_WRONG verdicts whose own derivations describe the TEST
   violating the spec ("The test uses case-insensitive keys... but
   the specification requires case-sensitive aggregation"). The
   label contradicts the reasoning. Consequences: the two QA tests
   were never quarantined, their one-shot adjudications were
   burned, and the FIXER received corrupt "fix the artifact" items
   against a provably-correct artifact.
B. **Unanchored paraphrase mints.** The final counted failure,
   stable for 3 cycles, is a REVIEW coverage complaint ("The test
   suite does not cover cases with empty in...") re-minted each
   cycle with a NEW text identity — no backticked test anchor
   (Stage 38 inheritance can't see it) and enough wording change
   to defeat Stage 35 text identity. Dispositions chase last
   cycle's copy; the new copy always counts.

## The change

A. **Coherence gate on adjudication.** The adjudication response
   gains a required field `violator`: "test" or "artifact".
   TEST_WRONG requires violator == "test"; ARTIFACT_WRONG
   requires violator == "artifact". On mismatch: ONE regeneration
   with the mismatch stated as the retry reason. Persistent
   mismatch (or missing violator after the retry) → the verdict
   is UNCERTAIN, a `test_adjudication_incoherent` event records
   both attempts, and the test does NOT consume its once-per-run
   adjudication (it stays eligible next cycle). UNCERTAIN by
   malformed response (Stage 33 behavior) still consumes nothing
   new — keep Stage 33 semantics except where this gate speaks.
B. **Paraphrase-tolerant identity (unanchored only).** At Stage
   35 set computation, for a discrepancy with NO test anchor:
   compute its content-token set (lowercased word tokens, minus a
   small stopword list, minus pure numbers/hashes). If its
   Jaccard similarity with the token set of any ledger item
   already dispositioned CLOSE — or with any other current open
   item — is >= 0.6, it is the SAME item: CLOSE-inheritance
   applies as in Stage 38 (event `discrepancy_paraphrase_closed`)
   or it merges into the open item (counted once). Anchored
   discrepancies keep Stage 38 behavior exactly; anchored items
   never merge via this rule.
   SAFETY: the verify-31 fixture test (55 items, 3 species) must
   still pass with its species distinct — this rule must not
   over-merge genuinely different complaints.

## Tests

- Adjudication: verdict/violator mismatch → one retry; coherent
  retry lands; persistent mismatch → UNCERTAIN + incoherent
  event + eligibility preserved for a later cycle.
- Coherent verdicts (both directions) behave as Stage 33/38.
- Paraphrase pair (two wordings of the empty-coverage complaint,
  per the evidence) merges/inherits at >= 0.6; a genuinely
  different complaint (different concept tokens) does NOT merge.
- The verify-31 fixture test still passes unchanged.
- Suite fully green (261 existing + new).

## Acceptance

Suite green; coordinator live run: no incoherent verdict can
quarantine or corrupt; the coverage-complaint paraphrase cannot
hold the gate open across cycles; trajectory reported against
verify-39's best 1. Zero-failure gate pass (AWAITING_COORDINATOR)
is the goal.

## Stage 39 implementation

- Added the required `violator` response field and verdict/violator coherence
  checks. A mismatch or missing violator on a directional verdict triggers one
  regeneration with the mismatch in the retry reason. Persistent incoherence
  becomes UNCERTAIN, records both raw attempts in
  `test_adjudication_incoherent`, and preserves eligibility for a later cycle.
  Retries use the existing reserve/meter/charge path; the three-candidate cap
  and Stage 33 malformed-response eligibility semantics remain intact.
- Added unanchored content-token Jaccard matching at the inclusive 0.6
  threshold, with durable wording observations and CLOSE inheritance via
  `discrepancy_paraphrase_closed`. Current open paraphrases retain their
  identities as aliases for disposition validation and count once. Anchored
  Stage 38 inheritance, budgets, and hidden grader handling are unchanged.
- Added nine tests covering coherent directions, retry recovery, persistent
  mismatch/missing fields, later eligibility, cycle caps, malformed response
  compatibility, paraphrase closure/merging, threshold boundaries, project
  isolation, and anchor exclusion. The verify-31 fixture test is unchanged
  and passed.
- Verification command: `python3 -m unittest discover -s tests`.
  Suite result lines: `Ran 270 tests in 18.997s` / `FAILED (errors=9)`.
  There were no assertion failures: 261 tests passed, including all new tests
  and the unchanged verify-31 fixture. Nine existing HTTP tests failed during
  localhost server binding with `PermissionError: [Errno 1] Operation not
  permitted` under the sandbox. No separate loopback/model-server calls were
  attempted. Full suite green remains unverified in this environment.
- `git diff --check` passed. Nothing was staged or committed.
- Agent token usage was not reported by the runtime.
