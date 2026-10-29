# Stage 46 — Probe-gated writes: a probe-failing artifact cannot be written

Branch: `codex/stage-46-probe-gated-writes` from main's tip (26ff25a).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-41 and verify-46, measured — same fingerprint)

Both runs died on gate fingerprint 0464cef01476ed28: the FIXER,
while repairing other items, rewrites count_words with a
systematic counting error (two observed variants: repeated words
overcounted by one; every word overcounted by one —
counts.get(word, 1) + 1-style initialization). Stage 42's golden
probes DID detect it — 14 probe_failed events in verify-46 — and
the gate's hidden grader agreed every time. Feedback was not
enough: the model cannot see the error in its own rewrite, and
kept shipping it across cycles until the defect clock escalated
both runs (best 5 and best 3). Syntax has a write-time gate
(Stage 31: byte-compile or reject). Semantics has none.

## The change

1. **Write-acceptance probes.** When a project declares golden
   probes (Stage 42) and a write targets an implementation path
   (CODER or FIXER write path), the runtime validates the
   RESULTING implementation set before accepting the write: the
   candidate content is staged with the current other
   implementation files, the probe runner executes, and any
   probe failure rejects the write — `write_rejected` event with
   reason `probe_failed:<probe name>` and the expected-vs-actual
   diff as the retry reason, consuming the same generation/retry
   machinery as a syntax rejection (Stage 31/32 semantics,
   including the Stage 45 chunked assembly: probes validate the
   ASSEMBLED file).
2. Probe validation applies to writes of implementation paths
   only; test files and other deliverables are unaffected. A
   project without probes is unaffected.
3. Probes remain feedback-only at the gate: gate accounting is
   unchanged (the hidden grader is still the only independent
   acceptance). The write path is the only new enforcement
   point. Probe declarations still never enter QA/CODER/FIXER
   generation contexts — the writer sees only the failing
   probe's diff in its retry reason, as it sees a syntax error's
   context today.
4. Stage 42's fixer-feedback probe runs remain in place.

## Tests

- A CODER/FIXER write of the +1-overcount artifact (both
  observed variants) is rejected with probe_failed naming the
  discriminating probe and the diff; a spec-correct write is
  accepted.
- Rejection consumes retries identically to syntax rejection
  (write_rejected event shape, generation accounting).
- Chunked FIXER writes are probe-validated after assembly.
- Writes to non-implementation paths never trigger probes;
  projects without probes write as today.
- Suite fully green (287 existing + new).

## Acceptance

Suite green; coordinator live run: the +1 artifact is
unwritable — the model must produce probe-passing counting
before any write lands; trajectory reported against verify-46's
best 3. Zero-failure gate pass (AWAITING_COORDINATOR) remains
the goal.

## Stage 46 implementation

- Added write-acceptance validation for CODER/FIXER implementation writes,
  after Python syntax validation and complete chunk assembly, before any
  project bytes, checkpoints, or accepted-progress receipts are published.
- Reused the Stage 42 probe runner with scratch-only candidate overrides
  alongside current other implementation files. Failures emit
  `write_rejected` with `probe_failed:<name>` and expected/actual values;
  the existing Stage 31/32 retry path receives only that failing probe's
  expected-vs-actual diff. Rejected writes preserve existing project bytes.
- Exempted test paths (including the repository's `test_` naming convention),
  non-implementation deliverables, and projects without probes. Gate
  accounting, probe declarations, generation contexts, and Stage 42 FIXER
  feedback runs remain unchanged.
- Added coverage for both overcount variants in both writers, retries and
  generation receipts, assembled FIXER candidates, multi-file scratch
  validation, scope exemptions, and retained post-write regression feedback.
  Legacy fixtures that deliberately publish probe-failing implementations
  now explicitly exercise projects without probes.
- Verification: `python3 -m unittest discover -s tests`.
  Suite result: `Ran 293 tests in 32.864s` / `FAILED (errors=9)`.
  All assertions passed; the nine errors are existing HTTP tests blocked by
  sandbox socket binding (`PermissionError: [Errno 1] Operation not permitted`):
  one test in `test_adversarial`, one in `test_harness`, and seven in
  `test_model`. No separate loopback or model-server calls were made.
  A fully green suite remains unverified in this sandbox.
- Token usage was not reported. Nothing was staged or committed.
