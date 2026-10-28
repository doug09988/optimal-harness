# Stage 20 — Plan sanitization: filter bad plan entries, don't poison the plan

Branch: `codex/stage-20-plan-sanitize` from the tip of `codex/stage-19-fixer-drive`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence (verify-21, 2026-10-28)

Driven CODER — g1 passes in verify-18 and verify-20 — failed all 3 generations
in seconds each with:
`coder_action_format_exhausted:plan; planned file not on declared write
allowlist: README.md`
The model's plan proposed a README (ordinary coder behavior); plan validation
rejected the WHOLE plan as a format failure, and every retry re-proposed it.
Latent since Stage 16; exposed by plan variance. The harness already knows
which files are writable — an un-writable entry is noise to filter, not a
verdict on the plan.

## The change

In driven CODER's plan handling (and FIXER's Stage-19 fallback plan, which has
the same shape):

1. Sanitize the parsed plan before validation: normalize a leading `./` on
   paths; drop duplicates (keep first); drop entries whose path is not on
   the stage's declared write allowlist, recording a board event
   `plan_entry_dropped` with {path, reason} for each drop.
2. Proceed with the surviving entries. Only if ZERO entries survive does the
   plan count as a model error (existing class/flow).
3. Everything else (per-file calls, self-check, resume) unchanged — resume
   keys should use the SANITIZED plan so a retry doesn't re-litigate drops.

## Tests

- Plan [README.md, wordfreq.py] completes: wordfreq.py written, a
  plan_entry_dropped event recorded for README.md, no attempt burned.
- Plan of only off-allowlist files fails with the model-error class.
- `./wordfreq.py` normalizes and is accepted.
- Duplicate entries collapse to one call.
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline.

## Stage 20 implementation

- Added shared sanitization for driven CODER plans and driven FIXER fallback
  plans: normalize a leading `./`, keep the first entry per path, and drop
  off-allowlist entries. Each dropped entry records a `plan_entry_dropped`
  board event with `path` and `reason`.
- Validate and persist the surviving plan, retaining the existing model-error
  flow for empty plans and existing per-file, self-check, and resume behavior.
  Structured FIXER findings retain their existing validation.
- Added scripted coverage for mixed writable/off-allowlist plans without an
  attempt retry, normalization, duplicate collapse, empty sanitized plans, and
  resuming sanitized plans in both flows.
- Verification: `python3 -m unittest discover -s tests`
  Result: `Ran 170 tests in 14.005s` — `FAILED (errors=9)`.
  All nine errors are existing HTTP tests failing to bind `127.0.0.1` with
  `PermissionError: [Errno 1] Operation not permitted` in this sandbox.
  All remaining 161 tests passed, including all CODER and FIXER tests.
  No separate loopback or model-server verification was attempted.
- Token usage: not reported by the session.
- No files staged or committed.
