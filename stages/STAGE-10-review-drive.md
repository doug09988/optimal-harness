# Stage 10 — Runtime-Driven REVIEW (re-spec)

Branch: `codex/stage-10-review-drive` from the tip of `codex/stage-09-stage-retries`.
Module focus: `optimal_harness/review.py` + the runtime wiring that invokes it.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox — live verification is the coordinator's job, in an unsandboxed shell.

## Why (evidence, not opinion)

REVIEW is the dominant live-run killer across six independent runs:
- verify-05 / verify-06: REVIEW action-format / section exhaustion.
- verify-07 / verify-08: a VALID write containing all four sections was rejected
  by the one-section-at-a-time protocol and charged as a failure of one section.
- verify-11: REVIEW failed after stage attempts on a repeated-action loop.
- tonight-gate-2 (2026-10-27, internal-weights lane, zero transport drops):
  INTAKE/REVIEW/CODER all passed at attempt 1; REVIEW-of-CODER then failed at
  generation 3 with `error:no_progress:repeated_action`, consuming all 3
  stage-9 attempts.

Stages 6, 7, 8 patched this protocol three times (section composition,
templates-in-denials, subset acceptance) and stage 9 added attempts. The defect
survived all of them, so per the project's escalation rule the protocol SHAPE is
the defect: a free action loop gives a small local model too many ways to repeat
itself, mis-envelope content, or re-litigate finished sections. The mechanisms
that DID work in this codebase all share one shape — the runtime owns structure,
the model supplies content (generated handoffs, pointer citations, obligation
ledger). Stage 10 applies that shape to REVIEW.

## The change

Replace REVIEW's free action loop with a runtime-driven sequence:

1. The runtime iterates the existing review sections in their fixed order. For
   each section it makes exactly ONE model call whose prompt contains: the
   section's charter question, the artifact under review (or the span the
   section concerns), any prior accepted sections it legitimately needs, and
   the output contract: "Reply with only this section's text. No JSON, no
   envelope, no other sections."
2. The model's ENTIRE response is the section content. The runtime validates it
   with the existing per-section validators and stores it as accepted.
3. An invalid section is re-asked (same section only) up to the existing
   per-section retry budget, with the validator's reason appended to the
   re-ask. Accepted sections are never re-asked and never re-presented as
   outstanding work.
4. After all sections are accepted, the verdict is ONE final constrained call:
   the first line of the response must be exactly `APPROVE` or
   `REQUEST_CHANGES`. Strict parse. An unparseable verdict is a model error:
   re-ask once; a second failure surfaces as the stage's model-error failure
   (stage-9 stage-attempt machinery may then retry the stage as today).
5. The runtime assembles the review artifact from the accepted sections +
   verdict in the existing artifact format, and the existing generated-handoff
   flow proceeds unchanged.

Robustness rules:
- If a section response arrives wrapped in an action-JSON envelope anyway,
  extract the section text from it when a plausible body field exists; count it
  as the section content and validate normally. If no body can be extracted,
  the attempt counts as an invalid section attempt (validator reason:
  `unextractable_response`).
- Identical text returned for two different sections is NOT an error (sections
  may legitimately agree); repetition can no longer stall the stage because the
  runtime, not the model, controls progression.
- Every section call, re-ask, validator verdict, and the verdict call are
  recorded as board events with the same shapes the current review flow emits,
  so the evidence trail stays comparable.

Out of scope (do not touch): INTAKE / CODER / INTEGRATOR / QA / FIXER action
loops; section validators' criteria; the review artifact's format; handoff
generation; obligations; budgets; stage-9 retry policy (REVIEW stage attempts
still exist, they just wrap a driven sequence now).

## Tests

- Adapt existing review tests to the driven protocol; keep the suite's
  scripted-model style.
- New: a scripted model returning envelope-wrapped section bodies completes
  REVIEW via extraction.
- New: a scripted model returning the SAME text for every section completes
  REVIEW (repetition is no longer an action loop).
- New: a scripted model whose first verdict line is garbage gets exactly one
  verdict re-ask; a valid second verdict completes the stage; two garbage
  verdicts fail the stage with the model-error class stage 9 retries.
- New: an invalid section is re-asked with the validator's reason present in
  the re-ask prompt; other sections are asked exactly once.

## Acceptance

Full suite green. The coordinator then runs the live word-counter pipeline;
success for this stage is REVIEW surviving real model behavior inside a
complete 9-stage run.

## Stage 10 implementation — 2026-10-28

Implemented runtime-driven REVIEW in `optimal_harness/review.py` and its runtime
wiring. The runtime asks the four existing sections in fixed order, retains
accepted values, and retries only invalid sections (three invalid submissions
per section) with the validator reason. Plain section text is normalized into
the existing scalar/list fields; action envelopes with extractable bodies are
accepted, and missing bodies report `unextractable_response`. Identical content
across sections cannot trigger action-loop repetition failures.

A final strict first-line verdict call accepts `APPROVE` or `REQUEST_CHANGES`,
with exactly one correction attempt. Exhaustion uses the existing
`review_action_format_exhausted:` model-failure classification so stage-9 retry
policy is unchanged. The runtime writes canonical review JSON containing the
accepted sections and verdict, then uses the existing generated-handoff and
stage-check flow. Model-call, accepted-section and denial events retain the
existing board event shapes. A REVIEW-specific system contract travels through
the existing queue to the client; other roles retain their original contract.
Validators, budgets, other action loops and stage-attempt policy are unchanged.

Adapted scripted review and pipeline fixtures and added coverage for plain text,
envelope extraction, identical responses, independent section retries,
unextractable responses, strict verdict retry/exhaustion, event counts, and
REVIEW system-contract serialization/transport using a mocked opener.

Verification: ran `python3 -m unittest discover -s tests`. The initial discovery
run encountered nine sandbox-denied socket-binding errors in existing HTTP
fixtures. For final discovery, a temporary `/tmp/stage10-no-network/sitecustomize.py`
guard raises `unittest.SkipTest` before any server bind, reserving these nine
HTTP tests for the coordinator. No production-server or live-model verification
was performed. Final invocation used
`PYTHONPATH=/tmp/stage10-no-network:$PWD PYTHONPYCACHEPREFIX=/tmp/stage10-pycache python3 -m unittest discover -s tests`.

Final suite result:
```text
Ran 116 tests in 10.100s

OK (skipped=9)
```

`git diff --check` passed. Nothing was staged or committed. CLI token usage was
not reported by the available execution tools.
