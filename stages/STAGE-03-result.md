# STAGE-03 result

Implemented CODER chunked writes on `codex/stage-03-coder-actions`.

- The coder charter now gives exact single-file, first, middle, and final chunk
  action skeletons. Each encoded action is limited to 4096 UTF-8 bytes including
  JSON escaping; smaller configured completion budgets lower this limit by
  reserving 512 bytes. Suggested data chunks are at most 2000 characters,
  reduced further for Unicode/escaping.
- Existing runtime/FileBroker append support is reused. Both write and append
  accept an optional strictly boolean `more` marker. Runtime permits this marker
  only for CODER; omitted means final, preserving existing actions.
- Partial files are checkpointed for integrity but remain missing in scheduler
  state. The next step explicitly requests append, and finish rejects any
  unfinished chunked file. Final append closes the file without replacing its
  earlier bytes.
- Invalid CODER JSON (including a string truncated at the completion cap) and
  complete actions exceeding the encoded size limit get deterministic size
  feedback demanding smaller chunks. No rejected action executes. The last
  successful observation is retained; three such errors in an attempt stop with
  `coder_action_format_exhausted`. Existing call/token/time budgets remain active.

Addresses the observed mid-string truncation, repeated invalid retries without
size guidance, oversized but parseable actions, and premature scheduler advance
following the first successful chunk. No automatic salvage of truncated data or
speculative second action is performed. Gate/verifier code, FileBroker policy,
plan spec, and example grader files are unchanged. Standard library only.

## Sandbox validation

- `python3 -m unittest tests.test_runtime -q`: **20 tests passed**, 0.493s.
  New coverage includes truncation/oversize recovery through successful handoff,
  UTF-8 append byte preservation, partial-file finish rejection, strict marker
  schema, three-error exhaustion for both truncation and oversize, and append
  allowlist/traversal/file-size enforcement with unchanged bytes after denial.
- `python3 -m unittest discover -s tests -q` (before adding the final two tests):
  **73 run; 64 passed; 9 errors**, 4.858s. Every error was sandbox
  `PermissionError: [Errno 1] Operation not permitted` while binding a test HTTP
  server: seven `test_model.ModelTests` cases, plus
  `test_actual_http_auth_roles_and_stale_lease` and
  `test_real_queue_serializes_loopback_calls_and_meters_cache`.
- Final sandbox-compatible discovery pass: **66 tests passed**, 5.043s.
  Used unittest discovery, recursively flattened the suite, and excluded only
  the seven `test_model.ModelTests` cases and the two named socket-bind cases
  above. This includes the scripted real-subprocess pipeline and hidden-oracle
  regression tests; it is not live-model acceptance evidence.
- `git diff --check`: passed.

No live run or model endpoint request was attempted. Coordinator must execute
live verification; model compliance and sufficiency of the unchanged total
attempt budget remain to be verified there. No files under ~/harness or ~/vault
were modified.
