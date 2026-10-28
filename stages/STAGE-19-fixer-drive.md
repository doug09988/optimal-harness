# Stage 19 — Runtime-Driven FIXER (the last action loop)

Branch: `codex/stage-19-fixer-drive` from the tip of `codex/stage-18-integrator-drive`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence (verify-20, 2026-10-28)

The pipeline's core is now DONE live: all nine stages (INTAKE, REVIEW, CODER,
REVIEW, INTEGRATOR, REVIEW, QA, REVIEW) SUCCEEDED at generation 1 in a single
run, in minutes. QA's verdict requested changes, the designed FIXER cycle
engaged — and FIXER, the last free-action-loop stage, failed all 3 generations
with `error:no_progress:repeated_action`. The run escalated inside the fix
cycle with the entire core pipeline green behind it.

Every stage converted to the driven pattern has passed at g1 live (REVIEW
repeatedly, CODER, INTEGRATOR, QA). FIXER is the final conversion.

## The change — driven FIXER (mirror coder.py/qa.py)

1. **Fix list from the record:** the runtime enumerates the requested changes
   from the artifacts that triggered the cycle (the QA report's failing
   findings and/or the REVIEW verdict's requested changes — use whichever
   structured source the current flow already produces; if the items carry
   target file paths, use them).
2. **Per-item calls** (cap 4,096): for each change item, one call whose
   prompt carries the finding, the CURRENT content of the target file, and
   the contract: "Reply with the complete corrected content of this file.
   No JSON, no envelope." The runtime writes the reply through the existing
   artifact path (split/checkpoint applies). Empty/invalid = one re-ask;
   a second failure marks the item `unfixed` and the sequence continues.
3. **Completion rule:** if any item ends `unfixed`, the stage fails with the
   model-error class (stage-9 retries apply, resumable per item 4). If all
   items are applied, the stage SUCCEEDS and the pipeline's existing
   re-REVIEW / re-QA flow proceeds as today.
4. **Resume across attempts** keyed by (project, stage, hash of the fix
   list + target files' current hashes): applied items persist; a retry
   redoes only unfixed/missing items. Changed inputs discard progress.
5. QA's acceptance tests remain uneditable by FIXER (existing protection):
   the driven writer's target set is exactly the fix list's files; the
   runtime must refuse a fix item whose target is the QA test file, as the
   loop version's scoping did.

Fallback: if NO structured fix list can be extracted, one plan call (cap
512) asks the model to enumerate `path :: change` lines from the QA/review
text, and the per-item flow proceeds from that list.

## Tests

- Scripted: two findings → two per-file calls → corrected files on disk
  byte-exact; stage succeeds.
- An item unfixed after its re-ask fails the stage with the model-error
  class after the other items were applied.
- Resume: attempt killed after item 1 of 2 resumes with only item 2's call.
- A fix item targeting the QA acceptance file is refused.
- Fallback plan call path works when no structured list exists.
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline. Success = a complete run
reaching AWAITING_COORDINATOR, including through a FIXER cycle when QA
requests one.

## Stage 19 implementation

- Added runtime-driven FIXER: extract path-bearing QA/review findings from the
  cycle brief, permitted reports, and latest trusted QA/review handoffs. Text-only
  findings use a bounded 512-token `path :: change` planning call.
- Each fix item receives its finding and the target's current complete content,
  with a 4,096-token completion cap. The runtime writes exact replacement bytes
  through the existing split/checkpoint writer. Invalid content gets one re-ask;
  exhausted items remain unfixed while later items run, then fail with the existing
  `fixer_action_format_exhausted:` model-error class.
- Added durable project/stage progress validated against the fix list, source
  records, and current target hashes. Applied-item progress and its write checkpoint
  share a database transaction. Retries skip applied items; changed inputs discard
  progress. FIXER targets are restricted to the requested, declared writable files;
  QA acceptance files and runtime metadata cannot be model-selected targets.
- Added 11 scripted FIXER tests covering exact bytes, split writes, caps, invalid
  replies, partial failure, retry/resume, changed targets, protected files, report
  extraction, fallback planning, and metadata scoping. Updated pipeline fixtures
  for driven FIXER and isolated legacy action-loop protocol tests from that driver.
  Both scripted full-pipeline tests reach AWAITING_COORDINATOR through FIXER.
- Other stage behavior, executor, budgets, and stage-9 retry policy are unchanged.

Verification: `python3 -m unittest discover -s tests`

```text
Ran 167 tests in 13.472s
FAILED (errors=9)
```

158 tests passed, including all 11 Stage 19 tests; no assertion failures. The nine
errors are existing HTTP tests whose local server bind fails with
`PermissionError: [Errno 1] Operation not permitted` in this sandbox (seven
`test_model` cases, the adversarial HTTP test, and the queue serialization test).
No standalone loopback/model-server verification was attempted. The complete suite
is therefore not green in this environment. `git diff --check` passed.

Token usage: not reported by this session. Nothing staged or committed.
