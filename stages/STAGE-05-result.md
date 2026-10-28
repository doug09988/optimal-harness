# STAGE-05 result

All four workstreams are implemented with standard-library code and tests. Worktree is complete and intentionally uncommitted: .git is read-only; no commits were attempted. No live model runs or model-endpoint requests were attempted. No changes were made to examples, their graders, ~/harness, or ~/vault. Existing untracked run artifacts were left alone.

## 1. All-role chunking — complete

Every role now receives the same encoded-action byte limit, deterministic size feedback, and three-attempt format/size exhaustion bound. Errors are named `<role>_action_format_exhausted`; CODER keeps its existing name and behavior. Oversized/malformed actions never execute. All roles can use write/append with boolean more markers; a partial file cannot finish. INTAKE and REVIEW JSON fragments are staged in memory and materialized only after the complete document passes its existing schema validation. Staging is bounded by the broker file limit. File scopes are unchanged.

Coverage includes all five additional roles exhausting oversized actions without writes, QA append assembly, INTAKE pointer JSON chunk assembly, REVIEW chunk assembly, and all unchanged CODER regression tests.

## 2. Pointer citations — complete

INTAKE slots accept `{slot, source, start_line, end_line, because}` in the slots map (or a slots list). Spans are 1-based and inclusive. Runtime extraction preserves exact source text and line endings and supplies canonical status/quote fields before writing the artifact. Duplicate/mismatched slots, non-integer/out-of-range/reversed spans, and whitespace-only spans are rejected. Runtime pointer sources must be the project specification and are read through the existing broker scope. The charter prefers pointers. Existing free-text normalization and both quotation gates remain in place.

Tests cover Unicode and multiline extraction, first/last boundaries, invalid and empty spans, actual runtime materialization from chunked pointers, and existing free-text invented/paraphrased evidence rejection.

## 3. Generated handoffs — complete, with explicit compatibility path

Workers are instructed to finish with summary and open_discrepancies rather than write handoff.json. The runtime generates status=ready, plan deliverables, supplied factual fields, and validated obligation resolutions; ready_document remains the readiness predicate. Missing/invalid finish fields produce bounded readiness feedback. Stage checks precede handoff_accepted; exhaustion produces handoff_error_reported without acceptance.

Compatibility decision: legacy hand-written handoffs and summary-only finish actions remain accepted under the previous checks, preserving existing examples, scripted queues, and unchanged CODER tests. New charters and scheduler instructions exclusively teach generated handoffs. This is a compatibility route, not removal of the old action capability or a change to write scoping. Legacy premature finishes retain their previous non-CODER failure behavior. Tests cover generated shape, missing-field correction, exhaustion, event ordering, and existing legacy behavior.

## 4. Obligation ledger — complete

New deterministic obligations.py extracts reference labels and conservative same-sentence duty predicates. Matching headings/captions or explicit HTML anchors resolve references. Stable IDs derive from kind plus normalized label content. Submission persists the ledger in the trusted project plan and vault/obligations.json; briefs include it without expanding file read permissions.

The gate checks archived successful handoffs from the current cycle. Each missing reference requires its label in CODER open_discrepancies. A duty requires an explicit discrepancy acknowledgment or an obligation resolution with a linked discrepancy or verbatim source evidence. Source evidence requires source/quote/because and is validated through scoped reads at generation and independently revalidated at the gate. Summary prose alone does not discharge a duty. Coordinator grading repeats obligation checks.

Required gap reports are exempted only in the runtime's obligation-aware handoff discrepancy check. The existing discrepancy-free predicate, REVIEW unresolved checks, private grader checks, citation checks, and other verifier predicates remain unchanged. An unrelated discrepancy continues to fail. Each unaddressed item contributes obligation_unaddressed:<id> to the gate result and FIXER brief. All six charters include the requested honesty line.

Tests cover missing Appendix B failing without the CODER flag and passing the obligation gate with it; existing headings/anchors creating no missing-reference item; Section/Figure/Table extraction; stable IDs; conservative duty extraction; explicit duty acknowledgment; rejection of fabricated evidence; ledger persistence; and unrelated discrepancy rejection. Gate-specific tests isolate the application grader with a deterministic verifier double; the existing scripted pipeline test also passes.

## Exact final sandbox validation

- `python3 -m unittest tests.test_runtime tests.test_obligations tests.test_real_pipeline -q`: **40 tests, OK**, 3.011 seconds.
- `python3 -m unittest discover -q`: **94 tests, 9 errors, 0 assertion failures**, 5.061 seconds. All nine errors are `PermissionError: [Errno 1] Operation not permitted` while binding local HTTP test-server sockets: all seven ModelTests, Adversarial.test_actual_http_auth_roles_and_stale_lease, and HarnessTest.test_real_queue_serializes_loopback_calls_and_meters_cache. These are sandbox restrictions, not passing tests.
- Standard-library unittest discovery with exactly those nine socket-binding tests excluded: **85 tests, OK**, 4.986 seconds. No test files were changed to skip these tests.
- `git diff --check`: **passed**, no output.

The coordinator must run the unrestricted full suite and live word-counter/ledger-audit checks independently. No live results are claimed here.
