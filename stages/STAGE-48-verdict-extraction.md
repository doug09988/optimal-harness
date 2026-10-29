# Stage 48 — Verdict parsing: balanced-object extraction (trailing-junk tolerance)

Branch: `codex/stage-48-verdict-extraction` from main's tip (1f2e6b8).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-49, measured)

Run ESCALATED in cycle 3 when the post-QA REVIEW failed all 3
generations: `review_action_format_exhausted:verdict;
unparseable_verdict: first line must be exactly APPROVE or
REQUEST_CHANGES`. The board's raw payloads show why: the model
returned complete, valid, compact JSON — 374 chars, verdict
REQUEST_CHANGES with real discrepancy_resolutions — followed by
ONE trailing period: `...}]}.`. parse_verdict's whole-text
json.loads fails with "Extra data: line 1 column 374", the
legacy fallback rejects the first line, and a single punctuation
character kills the run. (The rest of the stack worked: gate 5
with a probe-verified artifact, FIXER degraded with carry-over
exactly as Stage 47 designed.)

## The change

In parse_verdict (optimal_harness/review.py), after the
whole-text json.loads attempt fails, extract the FIRST balanced
JSON object from the response text and parse that:
- Scan from the first `{` with string-aware brace counting
  (braces inside JSON strings do not count; backslash escapes
  honored). The span ending where the depth returns to 0 is the
  candidate; json.loads it. On success, proceed exactly as the
  whole-text path does today (verdict + discrepancy_resolutions
  harvest).
- If no balanced object exists (including the truncated-JSON
  case), fall through to the existing legacy first-line path
  and raise exactly as today. Stage 37's rule stands:
  truncated JSON stays unparseable — extraction must not
  manufacture a verdict from an incomplete object.
- Nothing else changes: caps, contract text, legacy fallback,
  resolution harvesting.

## Tests

- The verbatim verify-49 shape: compact JSON verdict +
  trailing `.` → parses, verdict and resolutions harvested.
- JSON followed by a prose line; JSON wrapped in a markdown
  fence; prose preamble before the object — all parse via
  extraction.
- Truncated JSON (no balanced object) still raises
  PolicyError; garbage still raises; legacy plain-text
  verdicts still parse (Stage 36/37 tests keep passing).
- Suite fully green (298 existing + new).

## Acceptance

Suite green; coordinator live run: REVIEW verdicts survive
trailing punctuation; trajectory reported against verify-49's
cycle-3 REVIEW death. Zero-failure gate pass
(AWAITING_COORDINATOR) remains the goal.

## Stage 48 implementation

Added first-balanced-object extraction inside `parse_verdict`, only after
whole-text JSON decoding fails and before the existing legacy fallback.
The scan counts nested braces while honoring JSON strings and backslash
escapes, then decodes only the first complete candidate. Verdict validation
and discrepancy resolution harvesting use the existing path unchanged.
Truncated objects still raise; caps, contracts, and other parsers are unchanged.

Tests cover the verbatim verify-49 trailing-period payload, trailing prose,
markdown fences, preambles, nested objects, escaped strings, first-object
selection, invalid candidates, and truncated outer objects. Existing legacy
and truncated-JSON tests remain in place.

Verification: `python3 -m unittest discover -s tests`

```text
Ran 302 tests in 34.313s
FAILED (errors=9)
```

293 tests passed, including all verdict tests. All nine errors are sandbox
`PermissionError: [Errno 1] Operation not permitted` failures when existing
HTTP tests try to bind local servers (one adversarial test, one harness test,
and seven model tests). No separate loopback/model-server calls were made.
The full-suite green acceptance remains pending verification in an environment
that permits those test fixtures; coordinator live verification remains pending.

Token usage: not reported by the runtime. Nothing staged or committed.
