# Stage 16 — Runtime-Driven CODER (third application of the driven pattern)

Branch: `codex/stage-16-coder-drive` from the tip of `codex/stage-15-qa-resume`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence

The driven pattern is now 2-for-2 at transforming its stages: driven REVIEW
has passed at generation 1 in 10 of 10 live exercises across verify-13..17;
driven QA's mechanics worked on its first live exercise (verify-16 attempt 1
assembled and executed real acceptance tests; its deaths were call economics,
addressed by Stage 15's caps + resume).

verify-17 (2026-10-28) died at CODER, all 3 generations:
`error:no_progress:repeated_action` (g1, g2) and a deadline kill (g3). CODER
is the last big free-action-loop stage: the model loops identical actions —
re-running or re-writing without change — while the guard counts them toward
exhaustion. Stages 13/14 already removed its write-format problems (raw writes,
accept-and-split both absorbed live traffic in verify-16/17); what remains is
the loop itself.

## The change — CODER becomes a driven sequence (mirror qa.py's shape)

1. **Plan call** (cap 512): the model replies with the files it will produce,
   one per line: `path :: purpose`. Runtime parses (dedupe, order kept, cap
   24 files); unparseable/empty plan = one re-ask, then the model-error class.
2. **Per-file calls** (cap 4,096 each): prompt carries the spec sources, the
   intake artifact, prior accepted files' paths (not bodies, unless the file
   is small — follow qa.py/review precedent on context), and the contract:
   "Reply with only this file's complete content." The runtime writes the
   content through the existing artifact path (split/checkpoint machinery
   applies). Empty/invalid content = one re-ask for that file; a second
   failure marks the file `unwritable` and the sequence continues (the stage
   fails at the end ONLY if a file the plan marked essential is unwritable —
   treat every planned file as essential unless its purpose line says
   otherwise; simplest correct rule: any unwritable file fails the stage via
   the model-error class AFTER the remaining files are attempted).
3. **Self-check round:** after all files are written, ONE call asks the model
   for a single verification command as a JSON argv array (e.g.
   ["python3", "wordfreq.py", "sample.txt"]). The runtime validates it
   against the EXISTING executor allowlist and runs it via the existing
   executor. On failure, ONE repair round: for the file the model names in
   its repair reply (same call shape: `path` line + content), re-ask that
   file's content WITH the command's error tail in the prompt, rewrite,
   re-run the command once. A second failure ends the stage as SUCCEEDED
   with the self-check result recorded as failed in the handoff evidence —
   QA downstream is the real acceptance gate; CODER's self-check was never
   the gate. (If the model proposes a disallowed command, skip the
   self-check and record why.)
4. **Resume across attempts** (mirror Stage 15): accepted files persist
   keyed by (project, stage, hash of spec+intake inputs); a retried attempt
   re-uses accepted files and only calls for missing ones. Changed inputs
   discard stored progress.
5. The generated handoff proceeds as today. FIXER keeps the action loop for
   now (it is small, targeted edits; it has never run live yet).

Out of scope: INTAKE, INTEGRATOR (action loop + raw writes — it passes),
FIXER, executor allowlist contents, budgets, stage-9 policy, timeout values.

## Tests

- Scripted model: plan + per-file bodies completes CODER; files on disk are
  byte-exact.
- A file unwritable twice fails the stage with the model-error class after
  the other files were attempted.
- Self-check: failing command triggers exactly one repair round (the named
  file is re-asked with the error tail present in the prompt); second
  failure still completes the stage with the failure recorded.
- Disallowed self-check command is skipped and recorded, stage completes.
- Resume: an attempt killed after 2 of 3 files resumes with calls only for
  the missing file; changed input hash restarts fully.
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline. Success = a complete
9-stage run.

## Stage 16 implementation

- Added a driven CODER sequence: ordered, deduplicated file planning (24 files,
  512-token cap), complete per-file responses (4,096-token cap), and one re-ask
  for invalid plans or file content. Unwritable files fail through the existing
  model-error class after the remaining planned files have been attempted.
- File contents use the existing broker, UTF-8 storage splitting, and checkpoint
  path. Prompts carry spec/intake inputs, accepted paths, and small accepted files.
- Added a single self-check command proposal, matched exactly against existing
  configured argv allowlists. Disallowed commands are skipped with a reason.
  Failed checks get one named-file repair round and one rerun; results are
  recorded in handoff summary and evidence without making the check a QA gate.
- Added durable CODER progress keyed by project, stage, and input hash. Accepted
  files are restored and checkpointed across attempts; changed inputs discard
  stored progress. Plan order survives canonical JSON serialization.
- Updated scripted pipeline fixtures, replaced obsolete CODER action-loop tests
  with driven coverage, and retained shared action-loop coverage on FIXER.
  INTAKE/INTEGRATOR/FIXER runtime loops, executor allowlists, budgets, stage-9
  policy, and timeout values are unchanged.

Verification command: `python3 -m unittest discover -s tests`

Suite result:
```text
Ran 143 tests in 12.239s

FAILED (errors=9)
```

134 tests passed, including all nine new Stage 16 tests. The nine errors are
existing HTTP tests blocked at loopback server binding with
`PermissionError: [Errno 1] Operation not permitted` in this sandbox. The full
suite therefore cannot be reported green here. No separate loopback or
model-server verification was performed; live verification remains with the
coordinator.

Agent token usage: not reported by the session tooling.
