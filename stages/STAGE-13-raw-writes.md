# Stage 13 — Raw-payload writes (kill the JSON-escaping failure class)

Branch: `codex/stage-13-raw-writes` from the tip of `codex/stage-12-qa-drive`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence

The free-action-loop stages keep dying on ONE defect class — hand-serialized
JSON envelopes carrying large content:
- verify-10 (2026-10-27): INTEGRATOR burned 3 attempts on malformed action
  JSON (`"data",` instead of `"data":` inside a big write).
- verify-13: QA died the same way (`qa_action_format_exhausted`) writing
  test_acceptance.py — fixed for QA by Stage 12's driven protocol.
- verify-14 (2026-10-28): INTEGRATOR FAILED generation 3,
  `integrator_action_format_exhausted: three invalid or oversized actions`
  while writing `artifacts/integration_notes.md` — a prose markdown file.
  INTAKE/CODER scraped through on retries; REVIEW passed 1st-try again.

Stages 10+12 removed the class stage-by-stage for REVIEW and QA. CODER,
INTEGRATOR, and FIXER still write through JSON envelopes. Rather than driving
each of them (their work is less uniform), remove the root: file content should
never need JSON escaping at all.

## The change — a raw write form alongside the JSON form

1. A write action may arrive as: a FIRST LINE containing the action JSON
   WITHOUT a `data` field but WITH `"format": "raw"`, then a line containing
   exactly `---CONTENT---`, then the raw file content through end-of-message.
   The parser takes everything after the separator line as the literal content
   (byte-for-byte; it is never re-scanned, so content may itself contain the
   separator text on later lines).
2. Chunk semantics unchanged: `"more": true` means further chunks follow; the
   next message continues the same file. Raw and JSON chunks may mix within
   one file's sequence.
3. All existing validation applies identically after extraction: path
   confinement, per-chunk and per-file size caps (counted on the raw bytes),
   write scoping, checkpointing, events.
4. The JSON form remains fully accepted (back-compat). Malformed raw headers
   produce the usual templated denial, now showing the RAW form as the
   recommended shape for content-bearing writes.
5. Stage instructions for the action-loop writer roles (CODER, INTEGRATOR,
   FIXER) present the raw form as the DEFAULT for file writes, with the JSON
   form as the small-content alternative. Find wherever those instructions /
   denial templates live and update them consistently.

Out of scope: driven stages (REVIEW, QA) — untouched. Read/run/other action
kinds keep the JSON envelope (their payloads are small). Budgets, stage-9
policy, validators unchanged.

## Tests

- A raw write round-trips content dense with quotes, backslashes, newlines,
  and a literal `---CONTENT---` line inside the content.
- Mixed chunk sequences (JSON chunk + raw chunk) assemble one correct file.
- Raw chunk byte counts enforce the same caps (an oversized raw chunk is
  denied with the raw template shown).
- Existing JSON writes behave exactly as before (regression).
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline. Success = the remaining
action-loop stages surviving real model behavior inside a complete run.

## Stage 13 implementation — 2026-10-28

Implemented first-line raw write/append headers with `format: raw` and no
`data`, followed by the exact `---CONTENT---` separator. Payload extraction
preserves literal UTF-8 content, whitespace, newlines, and later separator
lines. Raw actions enter the existing write path, retaining mixed JSON/raw
chunk sequences, byte caps, scope checks, checkpoints, and events. Existing
JSON actions remain accepted.

CODER, INTEGRATOR, and FIXER now receive raw-default charter, system, scheduler,
and denial instructions; small-content JSON remains an alternative. REVIEW and
QA driven protocols and instructions are unchanged. Tests cover raw round-trip,
mixed chunks, UTF-8 chunk/file caps, scoping, malformed headers, raw denial
shapes, and JSON compatibility.

Verification: `python3 -m unittest discover -s tests`
Suite result: `Ran 130 tests in 11.150s` — `FAILED (errors=9)`.
There were no assertion failures; 121 tests passed. The nine errors are existing
HTTP tests blocked at socket binding by sandbox permissions
(`PermissionError: [Errno 1] Operation not permitted`). No separate loopback or
model-server probes were run. Full green verification remains for the coordinator
in an environment permitting the suite's local HTTP fixtures.

Token usage: not reported by the session.
Nothing staged or committed.
