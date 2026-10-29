# Stage 36 — REVIEW verdict parser must accept the Stage 35 JSON contract

Branch: `codex/stage-36-verdict-json` from main's tip (004f53c).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-36, measured from the board, 2026-10-28)

verify-36 aborted in cycle 1: the post-INTEGRATOR REVIEW failed all
3 generations with `review_action_format_exhausted:verdict;
unparseable_verdict: first line must be exactly APPROVE or
REQUEST_CHANGES`. The board's raw model outputs show why: Stage 35
changed the REVIEW response contract to a JSON object —
{"verdict": "REQUEST_CHANGES", "discrepancy_resolutions": [...]} —
and the model complied, returning that JSON (pretty-printed,
multi-line). The verdict PARSER still applies the legacy plain-text
rule (first line exactly APPROVE/REQUEST_CHANGES) on this path, so
every compliant response was rejected. A compact single-line JSON
verdict from another REVIEW call in the same run WAS accepted,
so the two format expectations coexist inconsistently. The
discrepancy machinery itself worked — the rejected payloads carry
real resolution entries with identity hashes.

## The change

1. The REVIEW verdict parser (every path that parses a review
   verdict, incl. the post-INTEGRATOR one) accepts, deterministically
   and in this order:
   a. A JSON object (compact OR pretty-printed, possibly surrounded
      by whitespace) with a "verdict" field equal to APPROVE or
      REQUEST_CHANGES — harvest "discrepancy_resolutions" from the
      same object into the Stage 35 ledger exactly as a native
      JSON response does today.
   b. The legacy format: first line exactly APPROVE or
      REQUEST_CHANGES (unchanged behavior).
   Anything else remains unparseable (same exhaustion path).
2. Unify the paths: whichever REVIEW call sites parse verdicts,
   they must all go through the same parser (no per-site drift).
3. No other behavior changes: budgets, exhaustion counts, the
   driven REVIEW section machinery, discrepancy semantics.

## Tests

- Pretty-printed JSON verdict parses; its discrepancy_resolutions
  land in the ledger.
- Compact JSON verdict parses (already worked — pin it).
- Legacy plain-text verdict still parses.
- A JSON verdict with an invalid verdict value is unparseable.
- Prose-first responses remain unparseable.
- Suite fully green (248 existing + new).

## Acceptance

Suite green; coordinator live run: the post-INTEGRATOR REVIEW
passes with JSON verdicts, discrepancy_resolutions from REVIEW
payloads appear in the run's ledger, and gate evaluations occur.

## Stage 36 implementation

- Extracted `parse_verdict` as the shared parser used by `DrivenReview.accept`,
  through which every runtime REVIEW call already passes, including reviews of
  INTEGRATOR. Compact and pretty-printed JSON with surrounding whitespace accept
  only exact APPROVE/REQUEST_CHANGES field values and pass the same object's
  discrepancy_resolutions to the existing Stage 35 ledger path. Legacy exact
  first-line parsing and the existing exhaustion behavior remain unchanged.
- Added parser coverage for both verdicts, both JSON layouts, legacy text,
  invalid JSON verdict values, and prose-first responses. Added scripted runtime
  coverage for CODER, QA, and post-INTEGRATOR REVIEW: both JSON layouts finish
  without retries and record STALE resolutions in the shared ledger. Invalid
  JSON/prose responses retain verdict exhaustion behavior.
- Verification: `python3 -m unittest discover -s tests`
  Result: `Ran 254 tests in 18.075s` / `FAILED (errors=9)`.
  All six new tests passed; the nine errors are existing HTTP server fixtures
  failing to bind local sockets with `PermissionError: [Errno 1] Operation not
  permitted` in this sandbox (test_adversarial, test_harness, and test_model).
  No separate loopback or model-server calls were run. Full-suite green and the
  coordinator live acceptance run remain unverified in this environment.
- Token usage: not reported by the runtime.
- Nothing staged or committed.
