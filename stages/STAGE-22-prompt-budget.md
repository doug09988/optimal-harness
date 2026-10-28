# Stage 22 — Prompt-size discipline for driven stages

Branch: `codex/stage-22-prompt-budget` from the tip of `codex/stage-21-transport-retry`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence (verify-23, 2026-10-28)

All 9 core stages passed (final REVIEW at g1 — Stage 17's deadlines held).
In the FIXER cycle, the server log shows a call prefilling a **59,050-token
prompt** (~89 tok/s → ~11 min of prefill alone). Its KV footprint blew the
lane's Metal working set: the request died with a connection drop, Stage 21's
client retried twice, and each retry re-sent the same poisonous prompt to the
same OOM. FIXER failed; the run escalated. Completion caps (Stages 11/15/18)
bounded what the model WRITES; nothing bounds what the harness SENDS. Driven
prompts assemble context (findings, target files, prior artifacts, evidence)
with no size budget.

## The change — a prompt budget with a shedding order

Applies to the driven stages' call assembly (FIXER first — it is the proven
poison — plus CODER per-file calls and QA per-test/report calls, which share
the assembly pattern):

1. **Budget:** an assembled driven prompt must not exceed **64,000 bytes**
   (the ledger already treats UTF-8 bytes as the conservative token proxy;
   ~64 KB ≈ ≤16-20k tokens worst case — safely inside the lane's working
   set alongside a capped completion).
2. **Shedding order** when assembly exceeds the budget, applied in sequence
   until under budget, each shed recorded in the call's evidence (event or
   metadata — match the codebase's existing pattern):
   a. Bodies of NON-target files → replaced by a pointer line:
      `path (N bytes, sha256 …)` — never their content.
   b. Evidence/report excerpts → truncated at 4,000 chars each with the
      Stage-17 truncation marker.
   c. Prior accepted sections/findings beyond the current item → pointers.
3. **The target file is never truncated** in a call whose job is to rewrite
   it (FIXER item calls, CODER file calls). If the target ALONE exceeds the
   budget, the item fails with reason `target_too_large_for_single_call`
   (model-error class; honest and rare) instead of sending a poisonous
   prompt.
4. The system/static prefix is exempt (it is shared and cache-friendly);
   the budget applies to the assembled variable context + task text.

## Tests

- A FIXER item whose context includes three fat non-target files assembles
  under 64,000 bytes, with pointer lines replacing bodies and the target
  file present in full.
- Evidence excerpts truncate at 4,000 chars with the marker during shed.
- A target file alone over budget → the item fails `target_too_large…`,
  and NO model call is issued for it (assert the scripted client saw no
  call for that item).
- Prompts already under budget are byte-identical to before (no behavior
  change on the common path).
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline. Success = a complete run
reaching AWAITING_COORDINATOR, FIXER cycle included.

## Stage 22 implementation

- Added a shared 64,000-byte UTF-8 limit for driven FIXER, CODER, and QA variable
  prompts; the static system prefixes are unchanged and exempt.
- Over-budget assembly sheds non-target file bodies to byte-count/SHA-256
  pointers, then caps evidence excerpts at 4,000 characters with the Stage-17
  marker, then replaces prior accepted material and other planned items with
  pointers. Each replacement records a `prompt_context_shed` event.
- Rewrite targets are protected from shedding. Oversized targets fail before
  dispatch with `target_too_large_for_single_call`, using the existing role
  action-format model-error classification. Unsheddable remaining context also
  fails before dispatch. Stage-9/21 policies and other budget amounts are unchanged.
- Added scripted regression coverage for target preservation, shedding order,
  UTF-8 size accounting, excerpt markers, unchanged small prompts, and oversized
  FIXER targets issuing no model call.
- Verification: `python3 -m unittest discover -s tests` — `Ran 183 tests in 13.962s`;
  `FAILED (errors=9)` (174 passed, including all Stage 22 regressions).
  The sandbox denies socket binding in nine existing HTTP-server tests; no live
  pipeline or model-server verification was performed.
- Token usage: not reported by this session.
