# STAGE-04 result

Worktree complete on `codex/stage-04-handoff`; no commit attempted because .git is read-only. Coordinator commits and runs the live verification. No live model run attempted; no changes to ~/harness or ~/vault.

## Exact mismatch

In runs/wordcounter-verify-02, the current CODER handoff is the runtime's replacement error report. The original 280-byte handoff is preserved in work/vault/checkpoints/2cec017e0ca538465e4430dff9293f7a7065d1d33aa99e9bed31c9b35901652d.blob and the action in board event 70. It contains status="ready" and summary, but omits open_discrepancies. Engine._check_stage requires status="ready" AND a list-valued open_discrepancies. The submitted plan requires wordfreq.py and test_unit.py; both were checkpointed (including test_unit.py at event 67). This failure was the missing handoff field, not a missing deliverable.

Event 77 records readiness failure; event 79's handoff_accepted has status="error". It was acceptance of the runtime's honest error report, not acceptance of a ready handoff before checking readiness.

## Changes

- CODER charter now gives the complete action/artifact skeleton, required fields, factual-summary guidance, and correction budget.
- Shared ready_document validation uses the same gate predicate: ready status and list-valued open_discrepancies. It gives field-specific errors for malformed JSON, non-object data, status, and discrepancies. No missing content is invented. Summary remains guidance, not a new gate requirement; nonempty discrepancy lists remain allowed and reported.
- CODER handoff writes must be complete writes and pass validation before bytes or checkpoints are accepted. Invalid writes receive corrective feedback. Three invalid writes/readiness attempts terminate with coder_handoff_exhausted; existing attempt budgets still apply.
- Finish rechecks the unchanged stage gates. CODER handoff-shape and missing-deliverable errors return named corrective feedback and override the scheduler's finish instruction. Other gate failures remain fatal, including integrity/citation failures and unfinished chunks.
- Error-report persistence emits handoff_error_reported. Successful runtime handoff_accepted still follows stage_checks_passed. Report storage, budget accounting, and lease fencing are unchanged.
- Tests cover the archived missing-field defect and recovery, bounded rejection without handoff checkpoints, error versus acceptance events, missing deliverables, finish revalidation, and preservation of the readiness predicate.

Plan, spec, examples/word-counter/grader, verifier rules, and write scoping were not modified. Standard library only.

## Sandbox validation

- `python3 -m unittest tests.test_runtime -q`: 25 tests, OK (0.692s).
- `python3 -m unittest discover -q`: 79 tests, FAILED with 9 errors (5.144s), before the final additional finish-revalidation test. All nine errors were PermissionError [Errno 1] Operation not permitted when binding a loopback HTTP test server: seven tests in tests.test_model.ModelTests, test_actual_http_auth_roles_and_stale_lease, and test_real_queue_serializes_loopback_calls_and_meters_cache. No assertion failures.
- Final discovery via unittest.defaultTestLoader.discover('tests'), filtering only those nine loopback-listener tests: 71 tests, OK (5.203s). This includes the final additional runtime test; 80 total discovered tests.
- `git diff --check`: exit 0, no output.

Existing untracked run inputs and stages/STAGE-04-prompt.txt were left in place. The coordinator must independently execute the live run outside this sandbox.
