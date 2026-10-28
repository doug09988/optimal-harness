BUILD REPORT — PARTIAL BUILD DELIVERED; ACCEPTANCE INCOMPLETE

Scope: isolated standard-library build under /Users/argus/optimal-harness only.
Production harness, vault and references are read-only. No installs, server restarts,
LaunchAgent edits, secrets changes, or production cutover.

Source evidence
SPEC.md read in full. Architecture and acceptance are the build contract.
Studied reliable-harness engine, store, policy, verifier, model, CLI, tests and
word-counter plan; production worker2 loop, ledger, lane; production harness.py
accept-and-flag handoff and deterministic check implementations.

Plan (written once)
1. Bootstrap one importable optimal_harness package from the supplied reference;
   preserve attribution. Contract: runnable CLI + reference baseline tests.
2. Evolve containment, verifier, budgets, durable artifacts, board/API and model
   supervision. Contract: all eleven mechanisms mapped to code and adversarial
   regression tests; default per-stage limits 60 calls/200000 fresh tokens/1800s.
3. Exercise tests, fix failures, and run word-counter against shared loopback
   model. Contract: real output saved locally; no simulated acceptance claims.
4. If foundational checks and word-counter are solid, attempt larger example.
   Contract: deterministic hidden-test-driven FIXER cycle, not a fabricated one.
5. Finish report with exact commands, counts, outcomes, limitations and review
   surface for Doofy. Builder runs checks but does not provide QA approval.

Builder resource bound: work in this single session; local model calls serialized;
no unbounded generation or retries. Individual stages obey production-scale budgets.

Status checkpoint 1
Implemented optimal_harness package, isolated examples, CLI, role-authenticated
loopback board API, fenced SQLite store, broker, runtime, verifier extensions,
and adversarial regressions. Reference baseline: 16 tests, 15 passed / 1 error
(Python 3.9 asyncio queue created outside an active event loop). Fixed by lazy
queue creation. Iteration 1: 16/16 passed. Iteration 2: 47/47 passed.
Commands and full outputs: runs/baseline-tests.log, runs/iteration-1-tests.log,
runs/iteration-2-tests.log. Each used:
  PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/runs" python3 -m unittest discover -s tests -v

Real-model attempt 1: runs/wordcounter-01, ESCALATED at INTAKE after 3 calls,
1498 fresh tokens and 1373 cached tokens (server reported). Model emitted
multiple speculative JSON actions; strict parser refused all of them. No
invented quotes were written or accepted. Protocol adjustment executes only
the first schema-valid JSON action; trailing speculative actions are discarded
and logged. Attempt 2 is running from a clean project directory.

Read-only live process discovery found the shared MLX server configured with
--prompt-cache-bytes 268435456, --prompt-cache-size 1, --decode-concurrency 1,
--prompt-concurrency 1. No server configuration was changed. New queues record
these observed settings and their hash; first two attempts predate automatic
process-configuration discovery and correctly record verified:false.

Status checkpoint 2
The suite now contains 66 tests. Latest executed run: 66 passed, 0 failures,
0 errors in 9.910s (runs/final-tests.log; will be rerun after final edits).
Added real subprocess/hidden-oracle integration: a scripted model builds an
implementation that incorrectly lowercases input; real unit and QA suites pass,
the real hidden grader fails, FIXER receives deterministic failure text, corrected
code passes the real hidden suite, and the project reaches AWAITING_COORDINATOR.
This is explicitly NOT a real-Qwen acceptance result.

Intermediate regressions were exposed and fixed: process-list shell parsing failed
on unmatched quotes (iteration 3: 54 tests, 4 errors), and Python 3.9 coroutine frame
clearing through a shared exception traceback broke queue shutdown (iteration 4:
55 tests, 1 error). Iteration 5: 63/63 passed; iteration 6: 64/64 passed. Logs retained.

Real Qwen attempts still have NOT reached AWAITING_COORDINATOR. Attempts 2 and 4
stopped on repeated reads; attempt 3 failed before submission on the process-list
parser; attempt 5 failed intake schema; attempt 6 was interrupted after 32 measured
calls in a read/write oscillation and recorded an honest CancelledError handoff;
attempt 7 was rejected for an invented quote; attempt 8 omitted a lifecycle slot.
Attempt 9 PASSED INTAKE, then failed REVIEW for an empty counterexamples list.
Added early schema refusal for INTAKE/REVIEW, structured object-valued JSON writes,
source-observation preservation across protocol errors, saved-progress briefs and
nonconsecutive thrash detection. Attempt 10 is running from a new clean root.

Implementation map (non-negotiable mechanisms 1–11)

1. Saved state, not conversation: engine.py assembles the copied, pinned role
   charter and task brief; runtime.py derives remaining deliverables from trusted
   checkpoints and sends only the last successful bounded observation plus a
   bounded protocol error when needed. model.py sends two messages per call:
   common static system prefix and current fresh user context. No previous model
   replies or conversational transcript are replayed. Charter/cache hashes and
   budgets are written into attempt-owned manifest.json.
2. Fresh metering: model.Completion uses prompt-cached+completion. ledger.py
   reserves conservative prompt bytes plus template overhead before dispatch;
   dynamic completion allowance is bounded by remaining fresh budget and an 8192
   default per-call ceiling. Store.attempt_usage persists exact reported usage
   by task/generation. Both ledger and board enforce breakers. Exact-cap usage
   stops the next dispatch without falsely claiming an overage; reserve stops
   are distinct from measured budget_exceeded. Failed requests lacking usage
   are explicitly unknown, not fabricated as zero-cost successful calls.
3. Write scoping: policy.FileBroker permits exact files, or explicit path/**
   scopes only. Reads/writes refuse traversal, absolute paths, symlinks and
   internal namespaces. Writable files are readable; other reads are explicit.
   Command outputs are copied back only for declared write mounts. Refusals are
   machine events; a model cannot overwrite those events or receipts.
4. Plan validation: verifier.validate_plan plus validation.prove cover file
   effects AND command effects. QA has no implementation file-read/write grant
   (command_only permits black-box execution); only QA writes acceptance tests;
   INTEGRATOR writes implementation only on exact wiring_paths; immutable inputs
   and machine receipts cannot overlap writes, including directory scopes.
   Graders are outside the project root; traversal/symlink/hardlink leaks are
   rejected. Command argv and executable hash are pinned by submission.
5. Pins/checkpoints: submission pins spec, fixtures, role charters, grading keys
   and referenced hidden test files. Every broker/model write checkpoints bytes
   to a content-addressed archive and durable task/generation record. The stage
   re-digests required deliverables and all its checkpoints. Gate and grade check
   latest file digests and archived content as well as input/grader pins. Ordinary
   writes/checkpoints run under a SQLite fencing transaction; stale generations
   cannot prepare, write, checkpoint, finish or hand off.
6. Evidence by reference: commands capture full stdout/stderr (no preview slice)
   into trusted attempt-owned commands/<name>.json plus the declared report alias.
   Both are checkpointed; the full receipt is readable but not writable by the
   owning model. macOS receipts also preserve exact bytes as Base64. Hidden
   verifier commands have separate trusted vault/verifier receipt files and
   checkpoint archives. Models receive receipt pointers, not injected command
   transcripts. Reports are encouraged to cite pointers; see the prose limit below.
7. Deterministic verification: checks.py supplies exists (via verifier), contains,
   not-contains, verbatim source/target slices, csv-count, csv-cell, csv-set,
   citations-verbatim and unchanged; reference JSON/intake/command checks remain.
   Private hidden acceptance is run independently. All tests are stdlib unittest.
8. Citation gate: stage completion recursively checks quote fields in JSON, JSONL
   and JSON code fences, requiring an exact permitted-source substring and a
   nonempty because clause. Invented quotation tests invoke the actual stage
   completion check. No model verdict substitutes for this check.
9. Role loop: INTAKE -> REVIEW -> CODER -> REVIEW -> INTEGRATOR -> REVIEW -> QA
   -> REVIEW -> GATE; gate failure routes FIXER -> REVIEW -> INTEGRATOR ... .
   REVIEW requires nonempty string test cases, observations and unresolved lists.
   Current-cycle handoff/review discrepancy lists are code-checked at the gate.
   Failure texts reach FIXER verbatim. Fingerprints use checker failures only,
   canonicalizing ephemeral paths/timings/addresses. Counts persist per fingerprint,
   even with intervening defects; three repeats escalate. Coordinator prose never
   becomes a deterministic fingerprint. Machine pass means AWAITING_COORDINATOR;
   only an explicit recorded coordinator grade can set DONE.
10. Containment/lane: there is no spawn tool. One trusted worker job is supported;
    a per-board lock excludes duplicate schedulers. FIFO plus an advisory lane
    lock serializes HTTP calls across package clients. A disposable subprocess
    bounds HTTP generation; queued requests expire rather than execute later.
    A failed generation marks the queue unhealthy. Recovery takes an explicitly
    supplied trusted restart hook followed by a real completion probe, never
    /v1/models. No real shared-server restart was attempted or configured here.
11. Honest failure: run-owned evidence and handoff skeletons precede model calls.
    Runtime errors/no progress/missing deliverables retain an error manifest and
    accepted handoff, including measured usage and budget flags. Stage/project
    failure is separate from successful delivery of the honest report. Invalid
    quotes and false completions cannot advance a stage. Trusted error-report
    replacement of an already-checkpointed handoff is itself re-checkpointed.

Required corrections

Paged reads use byte offset/length with per-call and encoded-observation budgets;
a coder can read the middle of a file larger than the page budget. UTF-8 trailing
partials are deferred, not replaced; next_offset advances by bytes actually returned.
Production default attempt budgets are 60 calls / 200000 fresh tokens / 1800 s,
with per-stage overrides and a 30-minute default lease renewed by heartbeat.
macOS commands use fixed argv, staged exact-file cwd jails and hash detection;
requires_isolation:true fails closed without Bubblewrap. The shared server's live
configuration was observed at a 256 MiB prompt-cache cap, one prompt/decode slot;
no host configuration was changed. Manifests record observed flags and their hash,
or verified:false when observation is unavailable. Prefix bytes are identical
across roles for a given package revision.

Layout and operator entry points

The single importable runtime package is optimal_harness/; tests/ and examples/
are alongside it. README.md documents commands, plan fields, check schemas, API
routes, containment and limitations. python3 -m optimal_harness --help was actually
exercised (exit 0; full output runs/cli-smoke.log). No installation is necessary.
The API exposes admin/verifier/worker bearer roles on loopback. The trusted local
runtime shares Store directly; the model never gets credentials or an SQL tool.
Local CLI/DB access is a trusted operator surface, not protected against the host
user. Doofy must review the broker, verifier, gate and run records before cutover.

Observed external edits

While building, the patch tool reported an external edit to runtime.py. I read
and preserved the on-disk changes: intake_document adds structural validation,
wraps an exactly-nine-slot root object without changing its values, and limits
invalid intake formats to three attempts. test_runtime.py also gained normalization
and retry-bound tests. I did not author those additions and cannot identify their
writer from file contents. They were included in the subsequent executed suite:
69 tests passed in 10.140s, with no package/test/charter source hashes changing
during that test run. Provenance is runs/final-test-provenance.json. No QA approval
or external contributor identity is inferred from that result.

Known gaps / deviations requiring coordinator review

- Real Qwen acceptance remains unproven until a run actually reaches
  AWAITING_COORDINATOR; scripted-model integration success is not substituted.
- macOS fallback cannot prevent absolute-path access, arbitrary syscalls, network
  access or process spawning inside model-authored code. It is documented
  degradation, not hostile-code isolation. Linux Bubblewrap was not exercised.
- Three-process architecture means board, trusted runtime and request/command
  subprocess; command workload descendants such as a test launching a CLI can
  exceed three OS processes. A literal system-wide three-process ceiling is NOT
  enforced. The package runtime itself supports only one worker job.
- Lane recovery is a tested library hook plus real-completion probe, not a shipped
  production restart policy. No LaunchAgent/server edits or restarts were allowed.
  Queue health is in-memory, not a durable cross-restart generation fence. Killing
  an HTTP client does not prove the remote local server stopped decoding.
- The API and worker share SQLite as trusted services; this is not a separately
  authenticated HTTP tool-execution daemon. The model has only broker tools.
- Citation parsing covers defined JSON/JSONL/fenced-JSON structures, not arbitrary
  prose quotations or single-quoted Python dicts. Literal fidelity and because
  presence do not prove semantic support. Free-form report prose is not a fully
  validated evidence-reference language and could still paste output; machine
  receipts remain the gate's authority. Coordinator semantic review is required.
- Command deadlines and process-group cleanup are exercised; a comprehensive
  macOS CPU/memory/disk quota sandbox is not implemented. Full output capture can
  consume substantial disk. Pinning detects protected-source changes but does not
  restore them after malicious degraded-mode commands.
- SQLite fencing is atomic against reclaim, but filesystem writes and SQLite are
  not one crash-atomic storage system. An interrupted commit may leave an
  uncheckpointed file, which is rejected rather than trusted on the next attempt.
  Scheduler restart repairs finish-before-enqueue gaps; production crash/migration
  soak testing has not been done. No production DB compatibility is claimed.
- Input/privacy validation detects declared path, symlink and same-file grader
  leaks. It cannot prove that a coordinator has not copied or paraphrased hidden
  keys into some otherwise permitted source. Coordinator plans are trusted inputs.
- First two real-model attempts predate automatic cache discovery and accurately
  retain verified:false; later successful discovery does not rewrite old manifests.

FINAL RESULT — PARTIAL BUILD, NOT ACCEPTANCE COMPLETE

Acceptance item 1: PASS. 69 tests executed; 69 passed, 0 failures, 0 errors;
10.140 seconds; exit 0. All named adversarial cases are present and executed.
Source hashes were unchanged during and after the final test run. This is builder
execution evidence, not independent QA or production approval.
Acceptance item 2: FAIL. No builder-launched real-Qwen run reached
AWAITING_COORDINATOR. Attempt 10 passed INTAKE and REVIEW, then CODER failed after
three malformed generations. Some code-generation replies exhausted the 8192
completion ceiling and repeated text; no valid implementation artifact was
invented or accepted. The final attempt's result.json reports a valid event chain
and no checkpoint integrity errors. The failure is retained, not relabeled success.
Acceptance item 3: NOT ATTEMPTED as a larger real-model example because item 2 was
not solid. The test suite DOES execute one genuine FIXER cycle against real
subprocess suites/hidden checks with a SCRIPTED model; that is not item 3.
Acceptance item 4: PENDING Doofy line-read/review. No production cutover, grade or
independent QA sign-off was performed. Production worker2 remains untouched.

Exact final test command (cwd /Users/argus/optimal-harness)

  PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/runs" python3 -m unittest discover -s tests -v

The last execution used a stdlib wrapper that hashed package/charter/test sources
before/after that exact unittest invocation and saved provenance. Full result is
reproduced below and saved at runs/final-tests.log. Source hashes and command are
in runs/final-test-provenance.json. Earlier 66-test output was preserved at
runs/pre-final-66-tests.log. The 67-test run was also executed (67 passed in 9.875s)
before the externally observed intake changes were included in the 69-test run.

Test history (all used the same unittest discovery command)
  baseline-tests.log: 16 run, 15 passed, 1 error (asyncio queue initialization).
  iteration-1-tests.log: 16 run, 16 passed.
  iteration-2-tests.log: 47 run, 47 passed.
  iteration-3-tests.log: 54 run, 50 passed, 4 errors (process-list parsing).
  iteration-4-tests.log: 55 run, 54 passed, 1 error (live coroutine traceback).
  iteration-5-tests.log: 63 run, 63 passed.
  iteration-6-tests.log: 64 run, 64 passed.
  pre-final-66-tests.log: 66 run, 66 passed.
  final-tests.log: 69 run, 69 passed, exit 0; no source changed during execution.
All historical failed outputs are retained; none are represented as passed.

Exact real-model example command, independently launched for NN=01 through 10

  PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/runs" python3 examples/run_local.py --run-dir runs/wordcounter-NN > runs/wordcounter-NN.log 2>&1

Endpoint: http://127.0.0.1:8080/v1
Model: mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit
Each attempt used a clean work root and copied private grader. Attempt 03 failed
before submission; attempt 06 was explicitly interrupted with SIGINT when its
read/write oscillation was observed (exit -2), and its runtime recorded the honest
CancelledError handoff. The remaining unsuccessful drivers returned failure rather
than AWAITING_COORDINATOR. The latest tracked driver (attempt 10) returned exit 1.

Builder-launched run results (server-reported known usage only)
  wordcounter-01: ESCALATED; calls 3/3 measured/started; fresh 1498; cached 1373; INTAKE: error:no_progress:invalid_actions
  wordcounter-02: ESCALATED; calls 4/4 measured/started; fresh 1204; cached 2877; INTAKE: error:no_progress:repeated_action
  wordcounter-03: NOT_SUBMITTED; calls 0/0 measured/started; fresh 0; cached 0; pre-submission process-inspection exception
  wordcounter-04: ESCALATED; calls 4/4 measured/started; fresh 846; cached 3483; INTAKE: error:no_progress:repeated_action
  wordcounter-05: ESCALATED; calls 5/5 measured/started; fresh 2772; cached 3256; INTAKE: intake_spec_gaps:intake unreadable: 'slots'
  wordcounter-06: ESCALATED; calls 32/33 measured/started; fresh 21921; cached 22715; INTAKE: CancelledError
  wordcounter-07: ESCALATED; calls 6/6 measured/started; fresh 4018; cached 3634; INTAKE: citation_check_failed:artifacts/intake.json: non-verbatim quote or missing because-clause
  wordcounter-08: ESCALATED; calls 4/4 measured/started; fresh 3423; cached 1924; INTAKE: intake_spec_gaps:intake slots must exactly equal pinned lifecycle matrix
  wordcounter-09: ESCALATED; calls 8/8 measured/started; fresh 5626; cached 4170; REVIEW: review_missing_counterexample_pass_or_schema
  wordcounter-10: ESCALATED; calls 13/13 measured/started; fresh 33633; cached 7947; CODER: error:no_progress:invalid_actions

Total builder runs: 10 directories, including the pre-submission failure.
Model calls with server usage: 79; started: 80; unmetered: 1.
Known fresh-token total: 74941. Known cached-token total, not billed: 51379.
The interrupted call's unknown usage is NOT included in those known totals.
These attempts were sequential. The server was a shared one-sequence resource;
there is no evidence here that every delay was generation rather than queuing.

Read-only audit: all builder-run event chains verify; submitted spec/fixture/grader/
charter pins still match. Old attempts 05, 07, 08 and 09 each retain a handoff
current-file/checkpoint mismatch caused by that older runtime writing its honest
error over a model-ready handoff without re-checkpointing. The final runtime now
re-checkpoints such trusted report updates; those old records are NOT rewritten.
They remain failures, not gate-approved projects. Full errors are in the audit JSON.

Two additional run directories appeared during the build; I did not launch them
and do not claim their results or stop their processes. Snapshot states:
  wordcounter-stage-02: ESCALATED
  wordcounter-verify-01: RUNNING
Their existence and the external source edits indicate concurrent activity.
wordcounter-stage-02 records a queue timeout before execution; this is not charged
as measured usage or represented as a successful model call. The running extra
attempt is not part of this builder's acceptance evidence. Final source hashes
were stable across the executed suite despite that external activity.

Review artifacts and reproducibility

  /Users/argus/optimal-harness/README.md
  /Users/argus/optimal-harness/runs/final-tests.log
  /Users/argus/optimal-harness/runs/final-test-provenance.json
  /Users/argus/optimal-harness/runs/example-audit.json
  /Users/argus/optimal-harness/runs/source-manifest.json
  /Users/argus/optimal-harness/runs/reference-diff.patch
  /Users/argus/optimal-harness/runs/wordcounter-10/result.json
  /Users/argus/optimal-harness/runs/wordcounter-10/board.sqlite3

The audit/diff were actually generated with:
  PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 python3 examples/audit_build.py
It reads databases read-only and writes summaries only inside runs/. The diff
compares package/charter files to the supplied reliable-harness reference and
includes new modules. The workspace is not a Git repository; no Git diff or commit
is claimed. It contains one runtime package with 22 Python/charter source files
and six test source files (including tests/__init__.py), as counted by the script.

Final test output, verbatim

test_actual_http_auth_roles_and_stale_lease (test_adversarial.Adversarial) ... ok
test_all_text_and_csv_verbs (test_adversarial.Adversarial) ... ok
test_budget_breaker_exactness_fresh_minus_cached (test_adversarial.Adversarial) ... ok
test_budget_failure_handoff_still_recorded (test_adversarial.Adversarial) ... ok
test_command_cannot_write_qa_tests_as_coder (test_adversarial.Adversarial) ... ok
test_command_timeout_is_honest (test_adversarial.Adversarial) ... ok
test_content_addressed_archive_tamper (test_adversarial.Adversarial) ... ok
test_default_production_budgets_and_dynamic_completion_cap (test_adversarial.Adversarial) ... ok
test_exact_scope_and_explicit_directory_scope (test_adversarial.Adversarial) ... ok
test_hardlinked_grader_plan_rejection (test_adversarial.Adversarial) ... ok
test_invented_citation_rejected_at_stage_completion (test_adversarial.Adversarial) ... ok
test_isolation_required_fails_closed (test_adversarial.Adversarial) ... ok
test_leaked_grader_plan_rejection (test_adversarial.Adversarial) ... ok
test_loopback_only_endpoint_and_no_spawn_schema (test_adversarial.Adversarial) ... ok
test_markdown_structured_quote_rejection (test_adversarial.Adversarial) ... ok
test_missing_because_rejection (test_adversarial.Adversarial) ... ok
test_missing_deliverable_rejection (test_adversarial.Adversarial) ... ok
test_paged_read_middle_of_large_file (test_adversarial.Adversarial) ... ok
test_post_checkpoint_tamper_detected_at_stage (test_adversarial.Adversarial) ... ok
test_real_command_detects_readonly_mount_mutation (test_adversarial.Adversarial) ... ok
test_real_command_full_untruncated_capture (test_adversarial.Adversarial) ... ok
test_receipt_tamper_detected_at_gate (test_adversarial.Adversarial) ... ok
test_receipts_never_model_writable (test_adversarial.Adversarial) ... ok
test_review_requires_counterexamples (test_adversarial.Adversarial) ... ok
test_scope_overlap_cannot_write_spec (test_adversarial.Adversarial) ... ok
test_stale_generation_cannot_commit_or_handoff (test_adversarial.Adversarial) ... ok
test_symlink_parent_and_leaf_escape (test_adversarial.Adversarial) ... ok
test_traversal_and_absolute_escape (test_adversarial.Adversarial) ... ok
test_undeclared_read_refusal (test_adversarial.Adversarial) ... ok
test_undeclared_write_refusal (test_adversarial.Adversarial) ... ok
test_wall_budget_exact_boundary (test_adversarial.Adversarial) ... ok
test_broker_confinement (test_harness.HarnessTest) ... ok
test_false_completion_is_cheap_failure (test_harness.HarnessTest) ... ok
test_full_stage_chain_then_external_coordinator_approval (test_harness.HarnessTest) ... ok
test_hash_chain_and_append_only_trigger (test_harness.HarnessTest) ... ok
test_independent_verifier_recomputes (test_harness.HarnessTest) ... ok
test_intake_citations_or_gaps_exact (test_harness.HarnessTest) ... ok
test_lease_reclaimed_and_fenced (test_harness.HarnessTest) ... ok
test_modified_grader_cannot_change_pass_condition (test_harness.HarnessTest) ... ok
test_modified_spec_cannot_change_acceptance (test_harness.HarnessTest) ... ok
test_plan_blocks_qa_source_and_fixer_test_edits (test_harness.HarnessTest) ... ok
test_plan_cannot_omit_trusted_suite (test_harness.HarnessTest) ... ok
test_real_queue_serializes_loopback_calls_and_meters_cache (test_harness.HarnessTest) ... ok
test_sandbox_fails_closed_if_unavailable (test_harness.HarnessTest) ... ok
test_structured_quote_cannot_be_fabricated (test_harness.HarnessTest) ... ok
test_three_identical_verifier_defects_escalate (test_harness.HarnessTest) ... ok
test_token_budget_circuit_breaker (test_harness.HarnessTest) ... ok
test_cache_config_discovery_records_actual_flags_not_assumptions (test_model.ModelTests) ... ok
test_deadline_kills_wedged_http_generation (test_model.ModelTests) ... ok
test_expired_queued_request_never_executes_later (test_model.ModelTests) ... ok
test_fresh_context_no_prior_chat_replayed (test_model.ModelTests) ... ok
test_invalid_server_usage_cannot_undercount (test_model.ModelTests) ... ok
test_missing_usage_cannot_be_invented (test_model.ModelTests) ... ok
test_recovery_requires_hook_and_real_completion_not_models_list (test_model.ModelTests) ... ok
test_real_hidden_failure_routes_fixer_then_machine_pass (test_real_pipeline.RealPipeline) ... ok
test_board_budget_breaker_is_durable_and_handoffable (test_runtime.RuntimeTests) ... ok
test_charter_tamper_stops_before_model_call (test_runtime.RuntimeTests) ... ok
test_declared_writable_alias_and_directory_deliverables (test_runtime.RuntimeTests) ... ok
test_discrepancy_schema_is_code_evaluated (test_runtime.RuntimeTests) ... ok
test_fingerprint_ignores_ephemeral_paths_and_timings_not_assertions (test_runtime.RuntimeTests) ... ok
test_fingerprint_repeat_counts_survive_intervening_defects (test_runtime.RuntimeTests) ... ok
test_intake_format_normalization_does_not_invent_evidence (test_runtime.RuntimeTests) ... ok
test_intake_format_retry_bound_never_checkpoints_invalid_document (test_runtime.RuntimeTests) ... ok
test_object_write_schema_feedback_preserves_source_without_chat_history (test_runtime.RuntimeTests) ... ok
test_pin_rechecked_before_coordinator_grade (test_runtime.RuntimeTests) ... ok
test_speculative_second_action_never_executes (test_runtime.RuntimeTests) ... ok
test_stale_worker_cannot_prepare_or_checkpoint (test_runtime.RuntimeTests) ... ok
test_unhealthy_lane_refuses_further_work_without_explicit_recovery (test_runtime.RuntimeTests) ... ok
test_utf8_pages_never_replace_or_skip_characters (test_runtime.RuntimeTests) ... ok

----------------------------------------------------------------------
Ran 69 tests in 10.140s

OK

END OF BUILDER REPORT. Independent review and real-model acceptance remain open.

