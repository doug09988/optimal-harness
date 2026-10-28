# STAGE-02 result

Implemented intake format recovery on `codex/stage-02-wordcounter`.

## Changes and rationale

- Added the complete nine-slot JSON skeleton to the intake charter, including explicit citation and gap alternatives. This addresses missing wrappers and scalar slot values seen in run 05.
- Added pre-checkpoint format validation of every slot. Malformed JSON, missing/extra lifecycle keys, scalar values, unknown statuses, empty fields, and attempts to append a partial JSON document receive deterministic feedback while retaining the last successful source observation.
- Accept an omitted `slots` wrapper only when the root has exactly the nine lifecycle keys. Values are preserved verbatim; no quote, explanation, or requirement is synthesized.
- Permit two corrections after malformed intake submissions; the third malformed submission escalates. Existing action repetition, token, call, and time limits still apply.
- Added regression coverage for normalization, malformed documents, bounded retries without checkpointing invalid artifacts, and unchanged rejection of invented quotations and unresolved gaps. Existing recovery coverage verifies successful correction with source context retained.

The worktree already contained lease and command-receipt changes when this task began. They are preserved in this commit: longer default lease, readable command captures, and attempt-owned command receipts alongside declared aliases. Every quote still goes through the existing verifier and every checkpoint still undergoes digest verification. `verifier.py`, plan validation, the gate, the example plan/spec, and grader were not edited.

## Validation and execution

- `python3 -m unittest tests.test_runtime`: 14 passed.
- `python3 -m unittest tests.test_runtime tests.test_harness tests.test_adversarial`: 59 passed, 2 errors solely because sandboxed loopback socket bind is forbidden (`PermissionError: [Errno 1] Operation not permitted`).
- The seven `tests.test_model` tests likewise cannot initialize their local HTTP servers in this sandbox.
- `git diff --check`: passed.
- Attempted `PYTHONPATH=. python3 examples/run_local.py --run-dir runs/wordcounter-stage-02` with the default endpoint/model. The run exited 1 after waiting for the shared model lock. Exact `result.json` project status: `ESCALATED`; INTAKE task status: `FAILED`; failure: `model_queue_timeout_before_execution`; audit valid: `true`; integrity errors: `[]`. No model completion executed. A direct request to the same local endpoint confirmed `ModelError: local model request failed: <urlopen error [Errno 1] Operation not permitted>`.

Live execution is stopped under the task's loopback restriction instruction. Coordinator execution outside this sandbox remains required; AWAITING_COORDINATOR is not claimed.

The pre-existing modified `runs/wordcounter-10/board.sqlite3-shm` and stage prompt were present or generated; my requested `git add -A` was blocked.

## Commit blocker

Attempted `git add -A` and `git commit -m "STAGE-02: recover intake format variance with bounded schema feedback"`. Both failed because this sandbox exposes `.git` as read-only: `fatal: Unable to create '/Users/argus/optimal-harness/.git/index.lock': Operation not permitted`. No commit was created. The coordinator must stage and commit the saved changes on the current branch outside this sandbox.

Concurrent workspace update observed afterward: HEAD is now `1f8b803` (`Stage 2: intake format robustness for word-counter (skeleton charter, slot validation, bounded format retries)`), containing the implementation and tests. That commit was created outside my sandbox. This updated result note and generated run records remain uncommitted; unrelated concurrent files were left alone.
