# Stage 24 — FIXER item resilience: one poisoned item must not kill the stage

Branch: `codex/stage-24-fixer-resilience` from the tip of `codex/stage-23-ratchet`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence (verify-25, 2026-10-28)

Pipeline golden through QA (38 acceptance failures — a weak first draft, but
a normal one). The FIXER stage then failed all 3 stage attempts with
`fixer_action_format_exhausted:unfixed; {"0":"file content must be nonempty
text without NUL or envelope"}`: the per-item call for item 0 returned
unusable content (empty after validation), the single re-ask failed the same
way, and every stage attempt repeated the identical death on the same item.
Forensics were hampered because driven-stage call responses are not
persisted anywhere — only hashes and the final denial string survive.

Three defects: (1) an empty/unusable reply gets one identical re-ask, not an
escalating correction; (2) one item's repeated failure fails the whole stage
even though the stage already tracks per-item `unfixed` state; (3) no raw
response evidence for driven calls.

## The change (driven FIXER; shared helpers if genuinely shared)

1. **Nudge-retries for unusable content:** when a per-item response fails
   content validation (empty, envelope-contaminated, NUL), re-ask up to 2
   additional times for that item; each re-ask appends an explicit
   correction naming the defect ("Your previous reply was empty. Reply with
   ONLY the complete corrected file content, no commentary, no code
   fences."). These re-asks charge the item's existing call budget
   accounting the same way format re-asks do today.
2. **Skip-and-disclose:** if an item is still unusable after its re-asks,
   mark it unfixed with its reason and CONTINUE with the remaining items.
   The stage SUCCEEDS with a partial fix when at least one item was fixed;
   the handoff's open_discrepancies lists every unfixed item and reason
   (the QA cycle downstream will re-test and re-queue what remains — that
   is the designed loop). If ZERO items could be fixed, the stage fails as
   today (`fixer_action_format_exhausted` with the per-item reasons).
3. **Response evidence:** persist each driven call's raw response
   (truncated to 4,000 chars, with a truncation marker) into the stage's
   evidence stream alongside the existing call records, for FIXER at
   minimum; extend to CODER/QA driven calls only if they share the same
   recording helper — do not build a parallel mechanism.

## Tests

- Item returns empty twice then valid content on the nudge retry → item
  fixed; the correction text is present in the re-ask prompt.
- Item permanently unusable + a second item fixable → stage succeeds,
  handoff lists the unfixed item with reason, fixed item's bytes written.
- All items unusable → stage fails with the per-item reasons map.
- Evidence contains the (truncated) raw responses for the item calls.
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline. Success = a complete run
reaching AWAITING_COORDINATOR.

## Stage 24 implementation

- Driven FIXER now permits two content nudge re-asks (three item calls total),
  naming empty content, NUL, code fences, or action envelopes in the correction.
  Re-asks use the existing reservation, metering, and charging path; budget
  limits and the Stage-23 ratchet are unchanged.
- Exhausted items remain unfixed while later items proceed. At least one applied
  item permits success; the handoff discloses every unfixed item by index, path,
  and reason, and its summary lists only applied fixes. Zero applied items still
  fail with `fixer_action_format_exhausted:unfixed` and the per-item reasons map.
  Only applied targets are checkpointed at finish.
- Every driven FIXER response, including planning and rejected item responses,
  is appended to the stage evidence JSONL with its call number, capped at 4,000
  response characters plus a truncation marker. Other stage flows are unchanged.
- Scripted tests cover recovery after two empty replies, defect correction
  prompts, partial success and written bytes, all-item failure reasons, and raw
  response evidence including Unicode truncation and empty responses.

Verification: `python3 -m unittest discover -s tests`

```text
Ran 191 tests in 14.439s
FAILED (errors=9)
```

All FIXER tests passed. The nine errors are existing HTTP test server binds
rejected by this sandbox with `PermissionError: [Errno 1] Operation not
permitted` (one adversarial test, one harness test, seven model tests). No
separate loopback or model-server verification was attempted. Full green-suite
verification remains for an environment permitting those existing tests.

Token usage: not reported by the runtime for this implementation session.
No files staged or committed.
