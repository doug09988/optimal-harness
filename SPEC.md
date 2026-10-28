# Optimal harness for a local model — build specification

Written by Doofy, 2026-10-27. Builder: Hermes + Codex (interim
lane, per Doug). Subject the harness will serve: the local
Qwen3-Coder-30B-A3B on MLX. Coordinator/verifier: Doofy.
Worker (production): the local model, serialized, one sequence
in flight. This spec synthesizes three sources of evidence:
worker2/harness.py production (deployed, 158 tests), the
Doug-supplied reliable-harness (audited 2026-10-27), and the
day's measured failure battery of the 30B.

## Design rule

Every mechanism below exists because a measured failure demanded
it. The worker is treated as an untrusted, capable first-draft
engineer: strong at drafting and assembly, weak at counting,
verbatim fidelity, and self-verification, and capable of
fabricating plausible evidence under completion pressure.
Nothing in the design asks the model to be better; the
environment makes the failure modes impossible, unforgeable, or
detectable.

## Architecture

One Python package, standard library only, three processes max:

1. **Board/API** (from harness.py, kept): SQLite task store,
   token auth (admin/verifier/worker), leases with fencing
   generations, heartbeats, per-attempt budget circuit breaker,
   accept-and-flag handoffs, append-only event log.
2. **Worker runtime** (worker2's successor): claims tasks,
   builds briefs from vault nodes, runs fresh-context model
   calls against the local OpenAI-compatible endpoint
   (loopback), brokers all tools, checkpoints deliverables.
3. **Verifier library** (from reliable-harness's verifier,
   extended): deterministic checks + plan validation; runs at
   stage completion, at handoff (QA chain), and at the gate.

## Non-negotiable mechanisms

1. **Agents are saved state, not conversations.** Each run's
   prompt = static prefix + role charter (vault file, hashed
   into the run manifest) + task brief + permitted pointers +
   last bounded observation. No chat history is ever replayed.
2. **Fresh-token metering.** Fresh = prompt − cached +
   completion, from server-reported usage; cached reads recorded
   but never billed. Budgets are per attempt; the ledger stops
   the run while a handoff is still submittable.
3. **Write-scoping (prevention).** A run may write only its
   brief's declared deliverables + declared `writable:` paths
   (exact files; directory scope only when explicitly declared).
   Enforced in the tool layer; refusals are logged evidence.
4. **Plan validation (from reliable-harness).** At submission,
   cross-role invariants are proven: QA cannot read/write
   implementation; only QA writes acceptance tests; INTEGRATOR
   touches implementation only via coordinator-declared wiring
   paths; machine receipts are never model-writable; spec and
   immutable inputs are unwritable by every role and command.
5. **Hash-pinning + checkpoint integrity.** Spec, fixtures, and
   grading keys are SHA-256-pinned at submission and re-verified
   before the gate and before the coordinator grade. Every
   deliverable is checkpointed (content-addressed archive) at
   write time; stage completion re-digests deliverables against
   checkpoints — modified-after-checkpoint fails the stage.
6. **Evidence by reference.** Command outputs are captured by
   the trusted runtime to run-owned files (full, untruncated);
   worker reports cite paths, never paste output. Captures are
   checkpointed and re-checked at the gate as receipts.
7. **Deterministic verification.** Check verbs: exists,
   contains, not-contains, verbatim (source↔target slices),
   csv-count, csv-cell, csv-set, citations-verbatim,
   unchanged (sha256 pin). Grading keys are held by the
   verifier, outside every worker read root. Anything code can
   check, code checks; model QA is an evidence sample, never
   the gate.
8. **Citation gate at stage completion.** Any structured quote
   in any deliverable is checked (verbatim substring of a
   permitted source + non-empty because-clause) before the
   stage may finish (reliable-harness's embedded-quote rule).
9. **Role loop with a code-evaluated satisfiability gate.**
   Stages: INTAKE → REVIEW → CODER → REVIEW → INTEGRATOR →
   REVIEW → QA → REVIEW → GATE; GATE failure routes to FIXER
   with the deterministic failure texts verbatim; the defect
   fingerprint is a hash of deterministic failures only; three
   repeats of one fingerprint escalate to the coordinator.
   REVIEW requires a non-empty counterexample list (schema
   enforced by code). Machine pass → AWAITING_COORDINATOR;
   only the coordinator's recorded grade closes a project.
10. **Containment without a spawn tool.** No subagent tool
    exists. Model calls happen only when scheduler code decides;
    one call in flight; lane recovery (restart + real-completion
    probe) is built in; generation wedges are detected, never
    trusted to /v1/models.
11. **Honest failure is cheap.** `error:no_progress`,
    `error:missing_deliverables`, honest error reports in
    evidence/handoff are successful run outcomes. Fabrication is
    the only unforgivable class and is graded as such.

## Corrections to reliable-harness (required deviations)

- **Readable files**: replace the 2,000-char observation cap
  with paged reads (offset/length) up to a per-call byte budget;
  a coder must be able to read the middle of a 20 KB file.
- **Budgets at production scale**: defaults per run of
  60 calls / 200,000 fresh tokens / 1,800 s, per-stage
  overridable; completion cap set per call from remaining
  budget, not a fixed 768.
- **macOS-viable command broker**: named, coordinator-pinned
  argv with cwd jail + per-file mount semantics where the OS
  allows (Bubblewrap on Linux); on macOS, argv allowlist + jail
  + hash-pins as the documented degradation — fail-closed only
  where a plan marks a command `requires_isolation: true`.
- **Prompt-cache discipline**: identical static prefix across
  roles; server cache capped by configuration (256 MB) and the
  run manifest records the server's cache configuration hash.

## Acceptance (the build is done when all hold)

1. The package's own test suite passes, including adversarial
   tests: undeclared read/write refusal, traversal/symlink
   escape, leaked-grader plan rejection, post-checkpoint
   tamper detection, invented-citation rejection at stage
   completion, lease-fencing (stale generation cannot commit),
   budget breaker exactness, missing-deliverable rejection.
2. The word-counter example (from reliable-harness) runs
   end-to-end against the local 30B through every stage to
   AWAITING_COORDINATOR.
3. A second, larger example — a multi-file Python tool with a
   hidden acceptance suite — completes the loop with at least
   one genuine FIXER cycle driven by deterministic failures.
4. Doofy line-reads the diff surface (tool broker, verifier,
   gate) and the run records before any production cutover.
   worker2 remains the standing worker until then; cutover is
   a separate, explicit decision.

## Out of scope

No direct agent-to-agent channels. No cloud model in the worker
path. No changes to the bioinformatics benchmark harness (it is
a separate artifact with its own design). No Hermes as a
runtime dependency — Hermes+Codex is the interim builder, not
part of the shipped system.
