# STAGE-08 result

Implemented on the existing `codex/stage-08-review-superset` branch, left complete and uncommitted. No .git writes or commits were attempted. Standard library only; no model endpoint access or live runs. No changes to examples/graders, ~/harness, or ~/vault. Pre-existing untracked `runs/verify08.out`, `runs/wordcounter-verify-08/`, and `stages/STAGE-08-prompt.txt` were left untouched.

REVIEW accepts any subset of its four sections in a complete write, including all four together. Each present known section is validated independently with the existing schema fragment. Valid sections are retained even when siblings fail; feedback includes templates only for invalid sections. Invalid rewrites preserve previously accepted values. Unknown keys are rejected by name and excluded from assembly without charging known sections; valid siblings are still accepted.

Only schema-invalid sections in a section submission spend their respective retry budgets. The third invalid submission retains `review_section_exhausted:<section>` and its template. Newly accepted sections reset generic repetition tracking. Existing action size limits, malformed-action/chunk protocol handling, canonical assembly, full-schema finish validation, and other gates remain in place. INTAKE, CODER, handoffs, and obligations are unchanged. The first observation, scheduler guidance, and REVIEW charter present multi-section writes as the normal path while preserving single-section submissions.

Tests cover all-four-in-one assembly and finish, mixed valid/invalid retention and invalid-only template feedback, repeated valid siblings spending no retries, mixed submission third-strike exhaustion of only the invalid section, unknown-key rejection without known-section charges, and the existing single-section and rewrite paths. The obsolete whole-document rejection expectation was replaced with malformed-document/chunk coverage.

## Exact sandbox outcomes

- `python3 -m unittest tests.test_runtime tests.test_real_pipeline -q`: **50 tests, OK**, 4.844 seconds. Includes the scripted pipeline with real subprocesses, hidden oracle, and FIXER routing. Output: `/tmp/stage08-targeted.log`.
- `python3 -m unittest discover -s tests -q`: **110 tests, FAILED (errors=9)**, 7.304 seconds; zero assertion failures. Output: `/tmp/stage08-full-suite.log`. All nine errors are `PermissionError: [Errno 1] Operation not permitted` during local HTTP server socket binding, before endpoint requests: all seven `test_model.ModelTests` tests; `test_adversarial.Adversarial.test_actual_http_auth_roles_and_stale_lease`; and `test_harness.HarnessTest.test_real_queue_serializes_loopback_calls_and_meters_cache`.
- Standard-library test discovery with `top_level_dir='.'`, flattened and filtered to exclude exactly those nine socket-binding tests without editing repository tests: **101 tests, OK**, 7.308 seconds. Output: `/tmp/stage08-sandbox-suite.log`.
- `git diff --check`: **passed**, no output.

The full suite is not green in this sandbox because socket binding is prohibited. No unrestricted full-suite or live-model acceptance claims are made.
