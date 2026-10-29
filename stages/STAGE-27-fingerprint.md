# Stage 27 — Defect fingerprint from failing test names only

Branch: `codex/stage-27-fingerprint` from main's tip (be109bb). Do NOT commit
(the coordinator commits). Do NOT run loopback/model calls from the sandbox;
live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence (verify-27 escalation record, 2026-10-28)

The gate escalated with: "three failed cycles on same defect:
reports/integration.json: suite is not green / reports/qa.json: suite is
not green / independent acceptance failed: t 0:…". The defect fingerprint
(failures.py) hashes the gate's failure STRINGS (lightly normalized). The
meta/rollup lines — "suite is not green", "independent acceptance failed",
report-file rollups — appear in EVERY not-green cycle regardless of which
tests fail, so cycles whose underlying failing-test sets genuinely differ
can hash as the same defect. The same-defect guard (3 repeats → escalate)
is meant to detect "the same named tests keep failing"; today it partly
detects "the suite is still not green".

## The change (failures.py fingerprint, and only there)

1. Extract failing TEST NAMES from each failure string: unittest-style
   identifiers in any of these forms — `FAIL: test_name (path.Class)`,
   `ERROR: test_name (path.Class)`, and result lines
   `test_name (path.Class) ... FAIL` / `... ERROR`. Collect across all
   failure strings, deduplicate, sort.
2. If at least one test name was extracted, the fingerprint is the hash
   of the sorted name set (keep the existing canonical/sha256[:16]
   scheme). Meta lines, rollup lines, timings, paths and prose contribute
   NOTHING.
3. If NO test name can be extracted (infrastructure failures, crashes,
   non-test errors), fall back to the CURRENT behavior (normalized
   failure strings) so those cycles still fingerprint deterministically.
4. Fixer notes remain VERBATIM (unchanged — only the hash input changes,
   which is failures.py's existing contract). The failure_cycle /
   escalation semantics in engine.py/store.py are unchanged: same
   fingerprint 3× still escalates; a changed failing-test set is a new
   fingerprint and a new cycle.

## Tests

- Two failure lists with identical meta lines but DIFFERENT failing test
  names → different fingerprints. (The regression case this stage fixes.)
- Same failing test names with different meta/prose/timings → same
  fingerprint.
- Test names embedded in a multi-line unittest output blob (the
  "independent acceptance failed" form) are extracted correctly.
- A failure list with no extractable test names fingerprints exactly as
  the legacy function did (fallback parity — compute both in the test).
- Suite fully green (201 existing + new).

## Acceptance

Suite green; coordinator runs the live pipeline and compares against
verify-27's pattern (18 → 14 → 14 → 14, escalated on repeats): success =
cycle detection demonstrably keyed to test names (an improving or
changing failing set does not burn the same-defect counter on meta
lines), with the full per-cycle gate counts reported either way.

## Stage 27 implementation

- Updated `optimal_harness/failures.py` to extract failing unittest identifiers
  (test method plus class path) from FAIL/ERROR headers and result lines,
  including multiline failure blobs. The hash uses the sorted, deduplicated
  identifier set when present; all other failure text is excluded.
- Preserved the existing normalized-string hash exactly when no failing test
  identifier is found. Fixer notes, engine/store cycle semantics, gates and
  budgets are unchanged.
- Added five tests covering changed failing sets with identical rollups,
  stable hashes despite changing prose/timings/order/duplicates, multiline
  acceptance output, distinct classes, and exact legacy fallback parity.
- Verification: `python3 -m unittest discover -s tests`
  Result: `Ran 206 tests in 14.500s` / `FAILED (errors=9)`.
  All five new tests passed. The nine existing HTTP tests errored while
  binding local test servers: `PermissionError: [Errno 1] Operation not permitted`
  under the sandbox. No separate loopback or model-server calls were made.
- Token usage: not reported by the runtime.
