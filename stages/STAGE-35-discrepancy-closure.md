# Stage 35 — Discrepancy closure ledger (Doug's "Stage 30" content)

Branch: `codex/stage-35-discrepancy-closure` from main's tip
(f457f6a) PLUS the Stage 34 branch merged in — check with the
coordinator's tree: branch from the stage-34 branch tip if main has
not absorbed it yet (the coordinator merges in order 34 then 35).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Enumeration evidence (verify-31, measured from primary records)

55 discrepancy items sit across verify-31's handoffs; the gate's
final count of 11 was composed of report rollups x2 each plus
'unresolved structured discrepancies:unresolved' x4 and
':open_discrepancies' x3 — criteria checking the same keys on
multiple docs. Three species exist:
1. QA-generation failures recorded as discrepancies, e.g.
   "test_empty_input_array: unwritable: reply must contain exactly
   the planned test function ..." and "test_output_file_format:
   unwritable: invalid Python: closing parenthesis ... line 35" —
   repeated IDENTICALLY across attempts/handoffs (stale copies),
   never retried to closure even when later cycles generate the
   test fine.
2. REVIEW prose observations (e.g. "count_words increments the
   count twice") recorded as open_discrepancies in stage handoffs —
   several stale or wrong for later artifacts, near-duplicated
   across attempts. NOTHING can close them: the verifier's
   resolution check needs obligation_resolutions linked to known
   obligation ids, and the run's obligations ledger is EMPTY —
   no stage ever writes resolutions. Structurally unclosable.
3. One live dispute (review.json unresolved: the case-sensitivity
   claim) — genuine content, also with no closure path.
Root causes, all three candidate classes confirmed: (a) no owner
closes them, (b) the same item multi-counts across checked docs,
(c) the resolution mechanism has no producer.

## The change

1. **Current open set.** At gate evaluation, the runtime computes
   the current open-discrepancy set: the union over (latest
   artifacts/review.json `unresolved`) + (the LATEST handoff per
   stage's `open_discrepancies`), deduplicated by normalized-text
   identity (whitespace/case-normalized hash). Older attempts'
   copies never count.
2. **Deterministic stale closure.** A discrepancy of the
   QA-generation species (references a test name as unwritable) is
   auto-dispositioned STALE when a test of that name exists in the
   current cycle's generated suite files. No model call.
3. **Resolution ledger.** A run-level `discrepancy_resolutions`
   record (list of {identity, disposition, note} — the shape the
   verifier's existing discrepancies-resolved check can consume,
   keyed by the normalized identity) is maintained by the runtime.
   Each cycle's FIXER brief gains a DISCREPANCY CLOSURE section
   listing every current open discrepancy not yet dispositioned;
   the FIXER's driven response must disposition each as CLOSE
   (note points at the closing evidence: file/test/result) or KEEP
   (still real — it becomes an explicit fixer work item that
   cycle). Undispositioned items stay open — silence never closes.
   REVIEW may also disposition items its own later passes supersede
   as STALE with a reason; keep this to the same ledger write path.
4. **Gate counting.** Discrepancy criteria count only items in the
   current open set that are undispositioned or dispositioned KEEP
   — each unique item ONCE. Closed/STALE items are reported in the
   cycle report for disclosure, never counted.
5. Hidden grader, adjudication/quarantine (Stage 33), budgets,
   ratchet semantics otherwise unchanged. Quarantined tests'
   related discrepancies can be dispositioned CLOSE with the
   quarantine event as the evidence pointer.

## Tests (including Doug's acceptance test)

- A fixture reproducing the enumerated verify-31 discrepancy set
  (the three species above, with the duplicated QA-generation
  items and stale prose) collapses to its unique identities; the
  QA-generation items whose tests exist in the fixture suite are
  auto-STALE; after a scripted FIXER closure pass the gate counts
  only the KEEP/undispositioned remainder — one count per unique
  item.
- An item copied across three handoffs counts once.
- Silence (no disposition) leaves an item counted.
- A CLOSE without an evidence note is rejected (stays open).
- Suite fully green (241 existing + new).

## Acceptance

Suite green incl. the fixture test; coordinator live run: the
cycle report shows the current open set, dispositions, and a
discrepancy count that reflects unique live items only; trajectory
reported against verify-35's tail (whose last counted failures
include these same entries).

## Stage 35 implementation

- Added a runtime-owned current discrepancy set: latest successful handoff per stage plus current `artifacts/review.json` unresolved entries, deduplicated with whitespace/case-normalized SHA-256 identities. Older handoffs do not contribute.
- Added deterministic QA-generation STALE closure using AST-discovered test functions in suite files checkpointed in the current cycle. An old suite file alone cannot close a discrepancy.
- Added a persistent run-level `discrepancy_resolutions` ledger with identity, disposition, and note, and audited ledger writes. FIXER receives DISCREPANCY CLOSURE work items and a driven closure response after corrections; missing/invalid dispositions remain open, and CLOSE requires an evidence note. REVIEW can submit reasoned STALE dispositions through the same write path.
- Replaced repeated document discrepancy criteria with one check per unique live identity, including the coordinator recheck. Cycle gate reports disclose the current set, the resolution ledger, and the unique live discrepancy count. Budgets, adjudication/quarantine, hidden grader files/paths, and ratchet rules were not changed.
- Added seven tests covering the 55-entry verify-31 species fixture, scripted FIXER closure, normalized copies across three handoffs, silence, missing CLOSE evidence, latest-handoff selection, current-cycle suite requirements, and REVIEW STALE closure. Updated the obligation test to distinguish obligation acknowledgment from discrepancy closure.

Verification: `python3 -m unittest discover -s tests`

```text
Ran 248 tests in 19.264s
FAILED (errors=9)
```

All seven new Stage 35 tests pass; there are no assertion failures. The nine errors are existing HTTP fixture tests whose local server socket binds fail with `PermissionError: [Errno 1] Operation not permitted` in this sandbox (one adversarial API test, one queue test, and seven model tests). No separate loopback/model-server verification was run. `git diff --check` passes. Nothing was staged or committed. Pre-existing Stage 34 working-tree changes were preserved.

Token usage: not reported by the session.
