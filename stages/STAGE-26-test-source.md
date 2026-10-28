# Stage 26 — Show the FIXER the failing tests (Doug's "Stage 25")

Branch: `codex/stage-26-test-source` from main's tip (545ab42). Do NOT commit
(the coordinator commits). Do NOT run loopback/model calls from the sandbox;
live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence (verify-26, 2026-10-28)

The pipeline converged 38 → 34 → 9 acceptance failures, then stalled: the
same 9 tests failed identically across consecutive cycles (long words,
mixed case, empty-string-only inputs, spaces in words, unicode, zero-args
exit code). These are spec-interpretation cases. The FIXER brief carries
only terse failure strings ("suite is not …", test names), so the fixer
keeps re-implementing its own reading of the spec while QA's tests assert
a different one. The exact expected behavior sits in the test bodies of
QA's generated acceptance file — which the fixer is forbidden to EDIT
(Stage 19) but has never been shown.

## The change

When the engine queues a FIXER cycle whose triggering evaluation either
(a) repeats the previous cycle's defect fingerprint, or (b) follows a
Stage-23 regression restore, the FIXER brief gains a section:

    FAILING TEST SOURCE — read-only. These tests define the expected
    behavior. Conform the implementation to them. Do not modify them.

containing, for each currently-failing test, that test function's source
extracted from the QA acceptance artifact (test_acceptance.py) for this
run, each truncated at 2,000 chars with the standard truncation marker,
and the gate's failure detail lines for those tests when available.

Rules:
- First-cycle briefs are byte-identical to today (no section) — the
  section is a repeat/regression escalation signal, not bulk.
- Extraction is by test name from the failing set; a failing test whose
  source cannot be found is listed by name with "(source unavailable)".
- Stage 22's prompt budget still governs the assembled brief: the test
  source section sheds FIRST (drop lowest-priority test bodies to
  pointers: test name + file path) before any existing context sheds.
- Stage 19's refusal stands unchanged: a fix item targeting the
  acceptance file is refused; this stage only READS it.
- The brief records in its evidence that the section was included and
  which tests' sources were attached (names only).

## Tests

- Scripted two-cycle run: cycle 1 brief has no test-source section;
  cycle 2 (same fingerprint) includes the failing tests' function
  sources verbatim (within truncation) and the read-only banner.
- A regression-restore cycle also triggers the section.
- A failing test missing from the acceptance file appears as
  "(source unavailable)"; other tests still attach.
- Oversized combined source sheds to pointers under the Stage-22 budget
  and the brief stays under 64,000 bytes.
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline. Success = a complete run
reaching AWAITING_COORDINATOR, or an honest escalation whose FIXER briefs
demonstrably carried the failing test sources on repeat cycles.

## Stage 26 implementation

- The engine attaches read-only failing acceptance function sources on consecutive
  defect-fingerprint repeats and Stage-23 regression restores. First-cycle briefs
  retain their previous contents. Regression briefs use the restored best
  artifact's failures.
- Acceptance artifacts are parsed without execution. Each named failing function
  is capped at 2,000 characters with the standard truncation marker; missing
  functions are identified as `(source unavailable)`. Matching gate detail lines
  accompany the excerpts, and brief evidence records inclusion and attached names.
- Both FIXER planning and file-rewrite prompts carry the section. Under the
  existing Stage-22 byte budget, lower-priority excerpts become name/file pointers
  before existing context sheds. Stage flows, gates, acceptance-file edit refusal,
  and budget constants are unchanged.
- Added scripted coverage for consecutive repeats, regression restores,
  nonconsecutive fingerprints, missing functions, truncation, oversized briefs,
  and source-first shedding.
- Verification: `python3 -m unittest discover -s tests`
  — `Ran 201 tests in 14.006s`; `FAILED (errors=9)`.
  All nine errors are existing HTTP fixture setup failures attempting to bind
  localhost: `PermissionError: [Errno 1] Operation not permitted`. No assertion
  failures; the Stage 26 tests passed. No separate loopback/model-server calls
  were made. Live pipeline verification remains with the coordinator.
- Token usage: not reported by this session.
