# STAGE-01 result

Root cause: Sharing the live `_pump` exception traceback with the request future lets Python 3.9's `unittest.assertRaisesRegex` call `traceback.clear_frames` on the suspended consumer coroutine, breaking its subsequent operation/shutdown and surfacing an asyncio RuntimeError. The supplied `optimal_harness/model.py` already prevents this in `_pump` by setting a fresh `ModelError(str(exc))` on the future rather than exporting the caught exception. No additional source change is warranted; the deadline polling, child kill/join, deadline assertion, and two-second elapsed-time assertion remain intact. A socket-free Python 3.9 probe exercising the existing queue error propagation, the same assertion context, and queue shutdown passed; it does not verify HTTP supervision.

Files changed (paths only):

stages/STAGE-01-result.md
runs/STAGE-01-model-tests.log
runs/STAGE-01-suite-tests.log

Exact final verification outcomes:

- `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/runs" python3 -m unittest tests.test_model -v`: FAIL; 7 run, 0 passed, 0 assertion failures, 7 errors. Log: runs/STAGE-01-model-tests.log.
- `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/runs" python3 -m unittest discover -s tests`: FAIL; 66 run, 57 passed, 0 assertion failures, 9 errors. Log: runs/STAGE-01-suite-tests.log.

Every error is `PermissionError: [Errno 1] Operation not permitted` when binding a local HTTP listening socket. The seven model errors are all tests in ModelTests: test_cache_config_discovery_records_actual_flags_not_assumptions, test_deadline_kills_wedged_http_generation, test_expired_queued_request_never_executes_later, test_fresh_context_no_prior_chat_replayed, test_invalid_server_usage_cannot_undercount, test_missing_usage_cannot_be_invented, and test_recovery_requires_hook_and_real_completion_not_models_list. The full suite additionally errors in test_adversarial.Adversarial.test_actual_http_auth_roles_and_stale_lease and test_harness.HarnessTest.test_real_queue_serializes_loopback_calls_and_meters_cache.

Remaining known issue: this execution sandbox denies socket binding and disallows permission escalation, so the real HTTP deadline test cannot reach its assertion and neither required command is green here. Independent verification in an environment permitting loopback listening sockets remains necessary. Historical logs were not treated as verification of this run.
