# Stage 28 — Un-truncate the gate's acceptance failure payload

Branch: `codex/stage-28-untruncate` from main's tip (4aff2b2). Do NOT
commit (the coordinator commits). Do NOT run loopback/model calls from
the sandbox; live verification is the coordinator's job. Suite:
`python3 -m unittest discover -s tests`.

## Evidence (verify-28, 2026-10-28, measured with the shipped extractor)

verifier.py line 188 builds the gate failure entry as
`"independent acceptance failed: " + (stderr or stdout)[-600:]` — only
the LAST 600 chars of the unittest output survive. Stage 27's
fingerprint extractor (failures.py `_FAILING_TEST`) therefore saw, per
gate cycle: cycle 1 → 1 test name, cycles 2/3 → 0 names (fallback to
legacy hash), cycles 4/5 → 1 name. The true failing sets (12–18 gate
failures per cycle) were invisible past the truncation horizon, so
same-defect detection keyed on ≤1 name.

## The change (verifier.py, acceptance-failure entry only)

Make the failure entry carry the COMPLETE set of failing test
identifiers from the acceptance run's full output, while staying
bounded:

1. Extract every unittest header `FAIL: <test> (<path.Class>)` and
   `ERROR: <test> (<path.Class>)` from the FULL stderr/stdout (headers
   appear once in the detail section; also accept the result-line form
   `<test> (<path.Class>) ... FAIL|ERROR` if present).
2. Compose the entry as: the existing prefix + the header list (one per
   line or comma-joined — your choice, but every header present and
   matchable by failures.py's `_FAILING_TEST` regex) + a bounded tail
   excerpt of the output for human context (the current [-600:] tail
   may be kept AFTER the headers).
3. If no headers can be extracted (crash, import error, empty output),
   keep the current behavior exactly (tail excerpt as today).
4. Nothing else changes: failure counts, gate pass/fail semantics,
   fixer notes (verbatim), failure-cycle logic, budgets. Total failure
   entry size stays bounded by the number of failing tests (headers are
   short); do not paste unbounded full output into the entry.

## Tests

- A synthetic acceptance output with ≥3 FAIL headers (and interleaved
  tracebacks long enough to push early headers beyond 600 chars from
  the end) yields a failure entry containing ALL headers, and
  failures.fingerprint over it extracts >1 identifier.
- Changing one failing test in the set changes the fingerprint;
  changing only traceback prose does not.
- Header-less failure output keeps today's tail behavior.
- Suite fully green (206 existing + new).

## Acceptance

Suite green; coordinator runs the live pipeline and re-measures the
per-cycle extracted name sets: success = gate cycles expose their true
failing sets (>1 identifier where >1 test fails), with gate counts and
escalation behavior reported either way.

## Stage 28 implementation

- Updated only the acceptance failure entry in `optimal_harness/verifier.py`:
  extract FAIL/ERROR detail headers and verbose result lines from both full
  output streams, deduplicate them, and prepend them to the existing
  600-character diagnostic tail. Headerless output keeps the exact previous
  prefix and stderr-or-stdout tail behavior.
- Added four mocked acceptance tests covering three failures buried beyond the
  tail, fingerprint completeness and sensitivity to changed test identities,
  invariance to traceback prose, both output streams and result-line forms,
  bounded payload size, and exact headerless fallback. Gate counts and receipts
  are also checked; gate semantics, verbatim fixer notes, and budgets are unchanged.
- Verification: `python3 -m unittest discover -s tests`
  produced `Ran 210 tests in 14.165s` and `FAILED (errors=9)`.
  All four new tests passed. The nine existing errors are socket-binding
  `PermissionError: [Errno 1] Operation not permitted` in this sandbox.
  No separate loopback/model-server verification was attempted.
- Token usage: not reported by the runtime.
