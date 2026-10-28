# Stage 17 — Size-aware call deadlines + resumable REVIEW + review prompt diet

Branch: `codex/stage-17-call-deadlines` from the tip of `codex/stage-16-coder-drive`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence (verify-18, 2026-10-28 — the 8/9 run)

Eight of nine stages SUCCEEDED (INTAKE, REVIEW, CODER, REVIEW, INTEGRATOR,
REVIEW, QA all at generation 1 — driven CODER passed in ~1 minute, driven QA
passed first-try with Stage 15 caps). The final REVIEW (of QA) failed all 3
attempts with `model_request_killed_on_deadline`.

The ledger's budget default is `max_call_seconds: 180` — a FLAT 180-second
kill for every call regardless of size. This review's calls are the fattest in
the pipeline (QA's package + acceptance evidence as the artifact under
review): a ~10k-token prefill plus a 2,048-token completion at this lane's
bad-window rates (~150 tok/s prefill, ~10 tok/s decode) legitimately needs
200-300 s. The call is not wedged; it is under-budgeted for its size. The
same flat wall caused verify-12's and verify-16's deadline kills.

## Changes

1. **Size-aware per-call timeout** in `Ledger.reserve` (the one place every
   call's timeout is computed): the returned timeout becomes
   `min(remaining_stage_seconds, max(max_call_seconds, ceil(prompt_bytes/150) + ceil(completion_cap/8) + 30))`
   where prompt_bytes counts static_prefix + prompt (the same bytes reserve
   already measures) and completion_cap is the cap being returned. Rates are
   conservative bad-window rates for this lane (150 tok/s prefill, 8 tok/s
   decode) plus a 30 s margin. `max_call_seconds` remains the FLOOR for small
   calls, so wedge detection on ordinary calls is unchanged; stage
   wall-clock, call-count, and token budgets still bound total exposure.
   Document the formula and rates in a comment where the budget defaults
   live.
2. **Resumable driven REVIEW** (mirror Stage 15's QA resume): accepted
   sections persist keyed by (project, reviewed task/stage, hash of the
   artifact-under-review inputs). A retried REVIEW attempt re-uses accepted
   sections and only calls for the missing ones, then the verdict call.
   Changed inputs discard stored progress.
3. **Review prompt diet:** any single source/evidence excerpt embedded in a
   REVIEW section or verdict prompt is truncated at 4,000 characters with a
   trailing marker line `[truncated — full text in <path>]` (path when the
   excerpt came from a file). This bounds prefill for evidence-heavy
   reviews (acceptance captures) without hiding that material exists.

Out of scope: budget amounts other than the timeout FORMULA above; other
stages' flows; validators; the executor.

## Tests

- reserve(): a small prompt+cap returns exactly the max_call_seconds floor;
  a 30KB prompt with cap 2048 returns the scaled value (assert the number
  the formula gives); the result never exceeds remaining stage seconds.
- Scripted REVIEW killed after 2 of 4 sections resumes on retry with calls
  only for the remaining sections + verdict, and completes.
- Changed artifact hash discards stored review progress (full re-run).
- A 10,000-char evidence excerpt enters a review prompt truncated at 4,000
  chars + the marker.
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline. Success = a complete
9-stage run (AWAITING_COORDINATOR).

## Stage 17 implementation

- Implemented the size-aware `Ledger.reserve` timeout formula using combined
  static-prefix/prompt UTF-8 bytes and the returned completion cap, bounded by
  remaining stage time. Documented conservative rates and the timeout floor
  beside the unchanged budget defaults.
- Added durable driven REVIEW progress, keyed by project, review task and
  reviewed stage, with a hash of complete permitted input bytes. Retries restore
  accepted sections and request only missing sections followed by the verdict;
  changed input hashes discard saved progress. Store writes retain lease fencing.
- Limited each REVIEW source/evidence excerpt to 4,000 characters plus the
  `[truncated — full text in <path>]` marker in section and verdict prompts.
- Added tests for the timeout floor, exact 486-second scaled deadline, remaining
  wall time, reduced completion cap, REVIEW retry after two accepted sections,
  changed-artifact invalidation, and excerpt boundaries in section/verdict prompts.
- Verification command: `python3 -m unittest discover -s tests`.
  Suite result: `Ran 148 tests in 12.716s` / `FAILED (errors=9)`.
  All 139 other tests passed, including the five new Stage 17 tests. The nine
  errors are existing HTTP tests blocked at localhost server binding by sandbox
  `PermissionError: [Errno 1] Operation not permitted`; no live model calls were
  made and no socket-binding retry or permission escalation was attempted.
- No budget amounts, other stage flows, validators or executor behavior changed.
  Nothing staged or committed. Coordinator live verification remains pending
  (`AWAITING_COORDINATOR`).
- Token usage: not reported by the runtime.
