# STAGE-06 result

Implemented on `codex/stage-06-review-sections`. No commits or .git writes were attempted. Standard library only; no model endpoints or live runs were used. No changes were made to examples/graders, ~/harness, or ~/vault. Pre-existing run outputs and the stage prompt were left untouched.

REVIEW now submits one top-level field per write to its declared deliverable, e.g. `{"action":"write","path":"artifacts/review.json","data":{"counterexamples":["Attempt a missing input path"]}}`. The named sections are `reviewed_stage`, `counterexamples`, `observations`, and `unresolved`. Each is validated immediately against its existing schema fragment. Receipts and fresh prompts identify accepted/missing sections. Sections can arrive in any order and be rewritten independently, including after assembly; a rejected rewrite preserves previously accepted content and the canonical artifact.

Only when all sections are accepted does the runtime assemble, fully validate, and checkpoint the canonical JSON. Assembly and the finish gate use the same shared full-schema validator, preserving the previous requirements and existing finish error name. Citation, handoff, checkpoint integrity, and independent verification gates remain in place. REVIEW no longer uses whole-document chunk staging or the global three-action format budget.

Invalid submissions are counted independently per section, without resetting another section's budget. The third invalid submission raises `review_section_exhausted:<section>`. Oversized or protocol-invalid actions use the identifiable single section when available; an unidentifiable malformed/whole-document action is charged to the scheduler's next missing section (or reviewed_stage after all sections are accepted). No rejected fragment is written. Each action remains subject to the existing byte bound. The charter and validation feedback explicitly require at least one nonempty ATTEMPTED counterexample even when no defect is found.

INTAKE shares the former structured whole-document staging path and retains its existing staging, validation, recovery feedback, charter, and retry behavior. CODER also requires a complete handoff object in its separate handoff validation path; it remains unchanged.

Tests cover section schema/feedback, independent rewrites, rejected rewrites preserving an assembled artifact, full assembly and finish, more than three total invalid submissions distributed across sections, named section exhaustion, oversized-section exhaustion, and rejection of whole-document/chunk submissions. Both scripted pipelines now submit sections individually; the mocked chain's expected call count is 39 (four reviews require twelve additional calls).

## Exact sandbox outcomes

- `python3 -m unittest tests.test_runtime tests.test_real_pipeline -q`: **42 tests, OK**, 3.716 seconds. This includes the scripted real-subprocess/hidden-oracle/FIXER routing test; it is not model acceptance evidence.
- `python3 -m unittest discover -s tests -q`: **102 tests, FAILED (errors=9)**, 6.491 seconds; zero assertion failures. All nine errors are `PermissionError: [Errno 1] Operation not permitted` while binding local HTTP server sockets: all seven tests in `test_model.ModelTests`, `test_adversarial.Adversarial.test_actual_http_auth_roles_and_stale_lease`, and `test_harness.HarnessTest.test_real_queue_serializes_loopback_calls_and_meters_cache`. No endpoint request was made by those blocked tests. These tests are not claimed as passing.
- Standard-library discovery (`discover('tests', top_level_dir='.')`) flattened into a TestSuite excluding exactly those nine socket-binding tests, without modifying or skipping tests in the repository: **93 tests, OK**, 6.360 seconds.
- `git diff --check`: **passed**, no output.

The unrestricted full suite remains unverified. No live claims are made.
