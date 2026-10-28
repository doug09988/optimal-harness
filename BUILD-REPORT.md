BUILD REPORT — in progress

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
