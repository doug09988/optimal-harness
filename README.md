Optimal harness — isolated builder artifact

Python standard library only. No installation required. Run commands from
/Users/argus/optimal-harness. Python 3.9+ is exercised on this Mac.
This is NOT production worker2 and is NOT approved for cutover.
Read SPEC.md and BUILD-REPORT.md for acceptance status and actual run evidence.

Quick start

  export PYTHONDONTWRITEBYTECODE=1
  export TMPDIR="$PWD/runs"
  python3 -m unittest discover -s tests -v

  PYTHONPATH="$PWD" python3 examples/run_local.py --run-dir runs/my-wordcounter

The run directory must not already exist. It contains a copied work tree and
private grader, SQLite board, submitted plan, task manifests and result.json.
The driver uses http://127.0.0.1:8080/v1 and the Qwen3-Coder-30B model ID.
It never approves its own result. Exit 0 means AWAITING_COORDINATOR, not DONE.

Operator CLI

  python3 -m optimal_harness.cli --db runs/board.sqlite3 submit \
    --root examples/word-counter/work --plan examples/word-counter/plan.json \
    --grader examples/word-counter/grader/acceptance.json
  python3 -m optimal_harness.cli --db runs/board.sqlite3 run \
    --endpoint http://127.0.0.1:8080/v1 \
    --model mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit --jobs 1
  python3 -m optimal_harness.cli --db runs/board.sqlite3 status
  python3 -m optimal_harness.cli --db runs/board.sqlite3 tasks wordcounter
  python3 -m optimal_harness.cli --db runs/board.sqlite3 audit

Prefer the copying example driver; direct submission runs in the supplied root.
The optional loopback board API uses a coordinator-supplied JSON credentials file:
identity -> {"role": "admin"|"verifier"|"worker", "token": <16+ characters>}.
Do not put production credentials in this repository or prompts.

  python3 -m optimal_harness.cli --db runs/board.sqlite3 serve \
    --auth-file runs/operator-auth.json --port 8431

POST routes: /submit (admin), /grade (verifier), /claim, /heartbeat, /handoff
(worker), /status and /audit (admin/verifier). Bearer auth is required. Worker
API handoff accepts-and-flags a report; it does not grant a stage-pass capability.
The trusted local runtime uses Store directly and owns broker execution and
stage completion. HTTP is an operator/board surface, not a model tool.
Grade is an explicit coordinator action only. The CLI is a trusted local operator
surface; possession of filesystem/DB access is outside the model threat boundary.

Layout and provenance

  optimal_harness/store.py        SQLite board, fenced leases, durable usage,
                                 handoffs, checkpoints, append-only hash chain
  optimal_harness/api.py          role-authenticated loopback HTTP API
  optimal_harness/engine.py       submission, briefs, stage checks, gate, grade
  optimal_harness/runtime.py      stateless role calls, tools, manifests, reports
  optimal_harness/model.py        loopback transport, serialized queue, deadlines,
                                 explicit recovery hook + real completion probe
  optimal_harness/server_config.py read-only live MLX cache configuration discovery
  optimal_harness/ledger.py       attempt budget and conservative dispatch reserve
  optimal_harness/policy.py       exact/directory file scopes, UTF-8 byte paging,
                                 atomic writes, Linux Bubblewrap broker
  optimal_harness/commands.py     named pinned argv, staged file jail, macOS fallback
  optimal_harness/verifier.py     deterministic checks and citation/intake validation
  optimal_harness/checks.py       production text/CSV check verbs
  optimal_harness/validation.py   cross-role plan effect proofs
  optimal_harness/failures.py     checker-only defect fingerprint canonicalization
  optimal_harness/charters/       versioned role state copied into project-local vault
  tests/                         adversarial, HTTP/model supervision, real pipeline
  examples/word-counter/         spec, fixture, coordinator plan and private oracle
  runs/                          real logs, databases, manifests and captured artifacts

Derived from the supplied references/reliable-harness modules and word-counter
example, with local modifications. The production harness.py/worker2 supplied
behavioral reference for budgets, leases, evidence and accept-and-flag reporting.
Neither reference tree is imported at runtime or modified. No Hermes dependency.
The project-local work/vault is contained in the run directory; it is NOT ~/vault.

Plan and tool protocol

Six roles are declared in the plan. Paths are relative POSIX paths. Exact files
are default. Explicit directory scopes use path/**; there are no implicit folder
grants. Command mounts must always be exact files. writable is an alias merged
into writes. Deliverables are exact nonempty files; fresh checkpoint authorship
is required for each stage, including repeated QA/INTEGRATOR cycles.

A model reply supplies one JSON action: read(path, optional byte offset/length),
write(path,data), append(path,data), command(name), finish(summary), fail(reason).
For JSON documents, write data may be a structured object/list (serialized by the
trusted runtime) to avoid double-escaping; append data remains text.
Only the first complete JSON object is eligible; any predicted trailing actions
are discarded and logged. No spawn, arbitrary argv, shell, browsing or grade tool.
A saved-state brief marks already-checkpointed outputs and remaining work; no chat
history is replayed. Identical actions repeated four times, even nonconsecutively,
stop as no progress. Invalid-action corrections are bounded to three.

Reads return whole requested pages, not silently truncated previews. Pages are
bounded by per-call raw and JSON-encoded observation budgets. UTF-8 partial trailing
characters stay for the next page; a misaligned starting offset is refused.
next_offset advances by actual UTF-8 bytes returned. Binary file reading is not a
model tool. Source hashes and command byte captures still operate on binary bytes.

Commands and actual containment boundary

Only coordinator-declared names select fixed argv. Submission pins executable
SHA-256 plus the complete plan in the event log. The command broker never accepts
worker argv, environment or cwd. It runs with a staged cwd containing exact mounts,
minimal environment, per-command timeout, and child-process-group cleanup. Output
is fully captured; macOS receipts contain both readable text and exact Base64 bytes.
Only declared command writes are copied back, under the lease fence. Mounted read
files and private grader files are checked for changes. Spec/fixtures/grader pins
are checked at the gate and before grade. Receipts/checkpoints are model-unwritable.

On Linux with available Bubblewrap, per-file mounts and network isolation are
requested. On macOS the fallback is NOT a security sandbox: Python code can access
absolute host paths, spawn processes or use network APIs. cwd, copied mounts,
allowlisted argv and hash checks prevent accidents/detect protected-input changes;
they do not prevent hostile code from escaping via OS syscalls. Mark any command
requires_isolation:true to fail closed without Bubblewrap. Linux isolation was NOT
exercised on this Mac. Do not run truly hostile code under the degraded policy.

The harness consists of board/API and trusted worker, with the verifier in-process.
One request subprocess bounds a model HTTP call. One worker job is recommended;
command workload descendants (e.g. unittest invoking the built CLI) are not included
in that architectural process count. See BUILD-REPORT for this qualification.

Budget/cache/lane behavior

Default attempt limits: 60 calls, 200000 fresh tokens, 1800 seconds. Stage budget
overrides are explicit. The completion allowance is min(configured per-call ceiling,
remaining fresh budget minus a conservative prompt-byte/template reserve), not 768.
Server usage is billed prompt-cached+completion. Cached tokens are separately stored.
Both the runtime ledger and board attempt_usage enforce budgets. Over-budget ordinary
commits are refused, while trusted honest report writes and handoff remain allowed.
Exact-cap usage stops the next dispatch; it is not falsely reported as an overage.
Failed requests without server usage are explicitly marked unknown, never estimated
as measured actuals. No automatic retry hides that unknown spend.

All roles share the same static system prefix. The manifest records charter hash,
budgets and observed live-process cache settings + hash. The shared server was
observed configured to 268435456 prompt-cache bytes and single-sequence concurrency.
This artifact does NOT change/restart the server or its LaunchAgent. If process
inspection cannot confirm a configuration, the manifest says verified:false.

Queue serialization uses FIFO plus a common advisory lock across package workers.
Other clients must cooperate with that lock or rely on the server's own one-sequence
queue. Killing an HTTP client does not prove a wedged server stopped decoding. A
failed generation marks the queue unhealthy; further queued work is refused until
an explicit trusted recovery hook and a real completion probe succeed. Recovery
is a library integration point; no production restart command is supplied or run.
The readiness path never treats /v1/models as proof of generation health.

Verifier schema

Each check is a JSON object with a type. Failures are code-generated. Supported
reference checks remain available (intake, citations, json_count, json_set_equal,
json_value, sha256, verbatim_equal/contains, sandbox_acceptance, command_receipt).
Production verb forms:

  exists: path, optional nonempty (default true)
  contains / not-contains: path, text
  unchanged: path, expected (SHA-256 hex)
  verbatim: source, target, optional source_slice / target_slice [start,end]
            (Unicode-character offsets, half-open; null end means EOF)
  csv-count: path, where {column: exact value}, expected integer
  csv-cell: path, where {...}, column, expected string; exactly one row required
  csv-set: path, where {...}, column, target (JSON list), optional target_key
           exact membership, no duplicate output entries accepted
  citations-verbatim: path, sources, optional claims_key (default claims)
  discrepancy-free: path, key (list must exist and be empty)

Structured quote fields in JSON, JSONL and JSON code fences require a permitted
source, exact nonempty substring and nonempty because clause before stage finish.
This is not a semantic proof that a quotation supports a claim; coordinator review
remains necessary. Free-form prose and single-quoted Python dictionaries are not
currently parsed as structured citations. Missing deliverables, invented citations
and post-checkpoint modifications cannot be converted into stage success.

The role chain is INTAKE -> REVIEW -> CODER -> REVIEW -> INTEGRATOR -> REVIEW -> QA
-> REVIEW -> GATE. Deterministic gate failure -> FIXER -> REVIEW -> INTEGRATOR ... .
Checker failure texts reach FIXER verbatim. Only checker failures enter the defect
fingerprint; ephemeral command cwd paths, unittest timings and memory addresses are
canonicalized for stable hashes. Three repeats escalate. Gate pass stops at
AWAITING_COORDINATOR. Only recorded coordinator grade can close the project.

Known limits and actual acceptance status belong in BUILD-REPORT.md. A scripted
model integration test with real suites and a genuine hidden-oracle FIXER cycle is
not a substitute for the required real-Qwen example. No production cutover occurs.
