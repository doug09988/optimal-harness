# Stage 42 — Golden probes: harness-owned ground truth in the fixer feedback loop

Branch: `codex/stage-42-golden-probes` from main's tip (3fdf1bc).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-41, measured)

Run ESCALATED at best 5 (6 → 5 → 21 → 6, defect clock at cycle 4).
Cycles 1-2 were healthy (hidden acceptance green). In cycle 3 the
FIXER, repairing other items, REWROTE the artifact and introduced
a pure regression: every repeated word overcounted by exactly one
(hidden assertion: A expected 2 got 3, b expected 2 got 3,
singletons exact). Cycle 4's FIXER shrank the blast radius but
never removed the core bug. The gate's hidden grader caught it
every cycle, but only at cycle end; the FIXER's own within-cycle
feedback (Stage 29) runs only the QA acceptance file — model-
authored tests that this cycle did not discriminate the defect.
On escalation the ratchet restored the best artifact
(artifact_restored), so the deliverable survived; the run didn't.
Structural gap: nothing harness-owned checks the artifact against
the SPEC between the FIXER's pen and the gate.

## The change

1. **Project golden probes.** A project may declare `probes` in
   its definition: a list of {name, input (JSON value written to
   the input file), expected_exit, expected_output (JSON value,
   compared after parsing the artifact's output file; omitted =
   exit-code-only)}. For the word-counter project, ship probes
   derived from its SPEC (authored from the spec text, NOT from
   any model output):
   - aggregation_case_sort: ["A","a","b","A","b","☃"] →
     [{A,2},{a,1},{b,2},{☃,1}] (codepoint order), exit 0.
   - duplicates_heavy: ["x","x","x","y","y"] → [{x,3},{y,2}].
   - empty: [] → [], exit 0.
   - singletons: ["one","two"] → [{one,1},{two,1}] sorted.
   - non_string_error: [1,2] → exit 2 (no output comparison).
2. **Probe execution in the feedback loop.** The runtime runs the
   probes (a) after the CODER stage completes and (b) after every
   FIXER pass, including the Stage 29 extra pass. A probe failure
   is a first-class signal: recorded as a `probe_failed` event
   (probe name, expected vs actual), fed into the FIXER brief /
   the Stage 29 feedback excerpt exactly like an acceptance
   failure, and counted in the fixer feedback loop's decision to
   grant the extra pass. Probes never run the hidden grader and
   never leak into QA/CODER generation contexts.
3. **Gate accounting unchanged** — probes are a feedback signal,
   not a gate criterion (the hidden grader remains the only
   independent acceptance). A probe failure at the final FIXER
   pass of a cycle simply reaches the gate as fixer_feedback,
   visible in the cycle record.

## Tests

- Probe runner: each shipped word-counter probe passes against a
  spec-correct artifact; the +1-overcount artifact FAILS
  aggregation_case_sort and duplicates_heavy with the expected
  diff recorded (regression test using the verify-41 bug shape).
- A FIXER pass that breaks a probe produces a probe_failed event
  and the failure appears in the next fixer feedback; the extra
  pass logic accounts for it.
- Projects without probes behave exactly as today.
- Probes do not appear in any model-facing generation context
  (assert the QA/CODER briefs are probe-free).
- Suite fully green (270 existing + new).

## Acceptance

Suite green; coordinator live run: a fixer-introduced regression
is caught within the cycle it is introduced, not at the gate;
trajectory reported against verify-41's best 5. Zero-failure
gate pass (AWAITING_COORDINATOR) remains the goal.

## Stage 42 implementation

Added the five SPEC-derived word-counter probe declarations and a harness-owned
runner for the project's INPUT.json / OUTPUT.json CLI (the first implementation
path is the entry point). Each probe runs through the existing command sandbox
with temporary input/output files and copied implementation files; scratch data
is removed after execution. Output JSON and exit codes are compared with the
project declarations, and mismatches emit `probe_failed` events containing the
probe name and expected/actual values.

CODER completion runs probes after readiness validation. FIXER runs them after
its initial pass and its Stage 29 extra pass, persists the check state for resume,
combines initial failures with acceptance feedback to grant the extra pass, and
records final probe feedback without granting another pass. The next FIXER brief
receives outstanding failures from the latest completed CODER/FIXER task; repaired
failures are not carried forward. Probe declarations and results stay out of
QA/CODER generation contexts. Gate criteria and the hidden grader path are
unchanged.

Added seven tests covering every shipped probe against a correct artifact,
the verify-41 +1 repeated-word regression and exact diffs, extra-pass feedback,
final failure propagation, CODER completion, generation-context exclusion, and
projects without probes. Legacy scripted runtime fixtures explicitly omit probes
to preserve coverage of probe-free projects.

Verification: `python3 -m unittest discover -s tests`

Suite result lines: `Ran 277 tests in 22.402s` / `FAILED (errors=9)`.
All seven new tests passed; 268 tests passed overall, with no assertion failures.
The nine existing HTTP tests errored at local server bind with
`PermissionError: [Errno 1] Operation not permitted` in this restricted sandbox.
No separate loopback/model-server calls were made. No files were staged or
committed. Coordinator live-run validation remains outstanding.

Token usage: not reported by this session.
