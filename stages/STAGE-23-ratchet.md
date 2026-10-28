# Stage 23 — The regression ratchet: fix cycles can only improve the artifact

Branch: `codex/stage-23-ratchet` from the tip of `codex/stage-22-prompt-budget`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence (verify-24, 2026-10-28 — first fully-mechanically-sound run)

Every stage passed at generation 1, the FIXER cycle engaged and re-ran
cleanly four times — and the artifact got WORSE. Deterministic acceptance
failure counts per QA pass: **9 → 38 → 28 → 24**. The driven FIXER's
full-file rewrites fix the flagged cases and silently break previously
passing behavior; each cycle's different failure fingerprint defeats the
existing same-defect escalation guard, so the loop wanders instead of
converging, and the best artifact the run ever produced (the original
CODER output, 9 failures) was discarded after cycle 1.

The mechanics are done. What the loop lacks is a ratchet.

## The change

Work in the engine/store layer where QA/gate results and cycle bookkeeping
already live (see engine.py's failure_cycle / fingerprint flow):

1. **Track best-so-far:** after each deterministic evaluation of the
   artifact (the acceptance/gate results the harness already computes),
   record (failure_count, artifact_snapshot_reference, fingerprint). The
   artifact bytes are already checkpointed by the existing machinery —
   reference the checkpoint, don't build a new store.
2. **Restore on regression:** if a cycle's evaluation has MORE failures
   than the best-so-far, restore the best artifact snapshot as the working
   artifact before the next cycle, and mark the cycle as a REGRESSION.
   The next FIXER brief must carry: the best failure count, the regressed
   count, and the restored artifact's failing items (the brief already
   receives failure notes — extend what it receives, same channel).
3. **Regressions count toward escalation:** a regression cycle increments
   the repeat counter for the BEST artifact's fingerprint (reuse
   failure_cycle semantics): three cycles that fail to beat the best —
   whether by regression or by repeating its fingerprint — escalate with
   the existing "three failed cycles" status, and the project record keeps
   the BEST artifact as its deliverable state.
4. Equal failure count with a different fingerprint is NOT a regression
   (no restore) — it is a new cycle as today, but the best-so-far only
   updates on a strictly lower count.

Out of scope: FIXER's call structure; QA; the gate's pass criteria; cycle
budgets elsewhere.

## Tests

- Scripted run: evaluation sequence 9 → 38 triggers a restore (working
  artifact == the 9-failure snapshot) and the next FIXER brief contains
  both counts.
- 9 → 9 (different fingerprint): no restore, best stays 9.
- 9 → 5: best updates to 5, no restore.
- Three non-improving cycles against a best of 9 escalate with the
  best-artifact state preserved in the project record.
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline. Success = a complete run
reaching AWAITING_COORDINATOR, or an honest escalation whose preserved
deliverable is the best artifact the run produced.

## Stage 23 implementation

- Added durable best-artifact bookkeeping containing the deterministic failure
  count, fingerprint, failing items, and references to existing implementation /
  FIXER-write checkpoints. Best state updates only on a strictly lower count.
- Gate evaluations record REGRESSION when the count increases, restore the best
  checkpoint bytes, and publish restored checkpoint references so working files
  and integrity checks agree. The FIXER notes include both counts and the best
  artifact's failing items. Bundled unittest failures/errors contribute their
  reported counts rather than counting the whole acceptance suite as one failure.
- Regressions reuse the best fingerprint's existing failure_cycle counter. The
  existing three-failed-cycles escalation preserves the best deliverable state.
  Equal counts with a different fingerprint keep the current artifact and do not
  replace the best. Stage routing, gate criteria, and budgets remain unchanged.
- Added five scripted tests covering 9 → 38 restoration and brief contents,
  9 → 9 with a changed fingerprint, 9 → 5 improvement, escalation with the best
  artifact preserved, and bundled acceptance failure counts.
- Verification: `python3 -m unittest discover -s tests`.
  Suite result: `Ran 188 tests in 13.890s` / `FAILED (errors=9)`.
  All five Stage 23 tests passed. All nine errors are existing HTTP-server tests
  failing to bind sockets with `PermissionError: [Errno 1] Operation not permitted`
  in this sandbox. No separate loopback or model-server calls were run.
- Token usage: not reported by the runtime.
- Nothing staged or committed.
