# STAGE-07 result

Implemented on the existing `codex/stage-07-denial-templates` branch. The worktree is left complete and uncommitted. No .git writes or commits were attempted. Standard library only; no model endpoints or live runs were used. No changes were made to examples/graders, ~/harness, or ~/vault. Pre-existing `runs/verify07.out`, `runs/wordcounter-verify-07/`, and `stages/STAGE-07-prompt.txt` were left untouched.

REVIEW's first tool observation lists all four sections with one complete JSON action per line, using the literal review deliverable path. A shared template builder supplies the actual prior-stage string, one nonempty ATTEMPTED counterexample string, and one-item observation/unresolved lists. Example content is explicitly identified as content to replace with factual work.

Every section rejection now includes the valid action for the section actually charged, including identifiable oversized actions and whole-document/unidentifiable actions charged to the next missing section. Templates precede diagnostic detail so ordinary diagnostic clipping retains the action. Terminal third-strike reports also carry the template. Accepted-section retention, validation predicates, action byte limits, retry accounting, budgets, and gates are unchanged. The REVIEW charter describes this feedback.

## Every denial path changed

1. REVIEW schema-invalid section writes (all four sections), including invalid section rewrites.
2. REVIEW whole-document, unknown-section, non-object, malformed JSON data, append, and chunk submissions rejected by the single-section workflow.
3. REVIEW protocol-invalid, unidentifiable, truncated, and oversized actions; identifiable single sections retain their own charge, otherwise the next missing section is charged.
4. REVIEW terminal `review_section_exhausted:<section>` reports.
5. Shared non-REVIEW action-format feedback and terminal action-format exhaustion, including CODER: a short parseable write action with the literal first deliverable path (or output.txt fallback when no deliverables are configured). INTAKE receives a complete slots-object template instead.
6. INTAKE complete-document schema denials: malformed JSON, missing/unexpected slots, bad status/slot value, missing or empty fields, and append instead of complete write. The template includes all nine required slots with valid gap records; instructions require actual gaps or citations rather than copying invented evidence.
7. INTAKE pointer/source/span denials use the same complete-document template while retaining existing source-derived correction context; structured-chunk and other deliverable-write tool denials also receive it. Terminal intake-format exhaustion includes it.
8. INTAKE identical-valid-rewrite denial now supplies the finish action because the artifact is already complete.
9. Shared recoverable finish readiness feedback (malformed generated handoff, malformed obligation resolutions, or missing deliverables when routed through that feedback) and terminal handoff exhaustion now include a minimal finish action with summary and open_discrepancies. Existing scheduler guidance still identifies missing deliverables; a template does not bypass gates or acknowledge real gaps automatically.
10. CODER direct handoff write denials (JSON/object/status/open_discrepancies and complete-write requirements) include a complete write action with the literal runtime handoff path and valid ready envelope. Its terminal handoff exhaustion includes the preferred runtime-composed finish action.

Direct validators remain diagnostic helpers; action templates are attached by the runtime where the actual task path is known. No validation rules were changed.

## Tests and exact sandbox outcomes

- `python3 -m unittest tests.test_runtime tests.test_real_pipeline -q`: **46 tests, OK**, 4.832 seconds. Includes scripted pipeline with real subprocesses/hidden oracle/FIXER routing; this is not live model acceptance evidence.
- `python3 -m unittest discover -s tests -q`: **106 tests, FAILED (errors=9)**, 7.224 seconds; zero assertion failures. All nine errors are `PermissionError: [Errno 1] Operation not permitted` on local HTTP server socket binding: all seven `test_model.ModelTests` tests, `test_adversarial.Adversarial.test_actual_http_auth_roles_and_stale_lease`, and `test_harness.HarnessTest.test_real_queue_serializes_loopback_calls_and_meters_cache`. They failed before issuing endpoint requests. Final full-suite output is in `/tmp/stage07-full-suite.log`.
- Standard-library discovery with `top_level_dir='.'`, flattened and filtered to exclude exactly those nine socket-binding tests without modifying repository tests: **97 tests, OK**, 7.250 seconds.
- `git diff --check`: **passed**, no output.

New tests parse the generated templates and validate their data, check the literal prior-stage value, check all four first-observation listings, exercise each section across schema/oversized/whole-document/unidentifiable rejection routes, and check INTAKE and CODER format templates. Existing handoff tests now validate the direct-write template and check the finish template.

The unrestricted full suite remains unverified because this sandbox prohibits socket binding. No live claims are made.
