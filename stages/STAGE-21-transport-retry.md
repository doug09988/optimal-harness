# Stage 21 — Transport retry at the model client (absorb lane drops)

Branch: `codex/stage-21-transport-retry` from the tip of `codex/stage-20-plan-sanitize`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence (verify-22, 2026-10-28)

The full pipeline ran green — all 9 core stages at generation 1, FIXER cycle
engaged — and then a single lane drop killed the run: FIXER FAILED at g1 with
`ModelError: local model request failed: Remote end closed connection without
response`, a class Stage 9 deliberately does not retry at the stage level.
The server log confirms the mechanism: its request-exception count grew
26 → 36 since last night, still dominated by Metal "Insufficient Memory"
command-buffer failures — now reproduced with the weights on the internal
SSD, so the T7 is exonerated; this is GPU working-set pressure on long runs
and it will recur at some low rate forever.

An inference call is idempotent — a drop costs a re-send, not correctness.
The right absorption layer is the client, below every stage: stage attempts
should never see a transient transport failure.

## The change — LocalChatClient retries transient transport failures

1. In the model client (optimal_harness/model.py), classify as TRANSIENT:
   connection closed without response, connection reset, broken pipe, and
   connection-refused/aborted errors raised by the HTTP layer in the local
   client (NOT deadline/timeout kills — those keep today's behavior, and
   NOT HTTP error statuses or malformed responses).
2. On a transient failure, retry the SAME request up to 2 additional times
   (3 attempts total), sleeping 5 s before the first retry and 15 s before
   the second. Each retry is the same prompt/cap — the request is its own
   probe; no separate health check.
3. Record retries so they are visible in run evidence (a counter/field on
   the completion or attempt metadata the runtime already persists — use
   the least invasive existing channel; if none exists, a board event
   `model_call_transport_retry` with attempt number).
4. If all 3 attempts fail transiently, surface the ModelError exactly as
   today. Token accounting: a dropped attempt's usage is unknown today
   (the runtime already flags `usage_unknown_for_failed_request`); retries
   follow the same accounting path — do not invent usage for dropped
   attempts.

Out of scope: stage-9's retry allowlist (unchanged); deadline behavior;
server configuration; budgets.

## Tests

- Scripted transport failing transiently twice then succeeding → the call
  succeeds; 2 retries recorded.
- Scripted transport failing 3× transiently → ModelError surfaces, 2
  retries recorded.
- A deadline/timeout failure is NOT retried at the client (surfaces
  immediately).
- An HTTP 500 response is NOT retried (surfaces as today).
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline. Success = a complete run
reaching AWAITING_COORDINATOR.

## Stage 21 implementation

- Added narrowly classified client transport retries: at most three attempts of
  the same Request, prompt, and completion cap, with 5 s and 15 s backoffs.
  HTTP statuses, malformed responses, and timeouts are not retried.
- Added completion retry metadata and unknown-failed-usage flag without inventing
  dropped-attempt token usage. Child retry notifications cross the existing pipe;
  the parent retains its original absolute deadline and kill behavior.
- Added six socket-free scripted tests covering success after two drops,
  exhaustion, non-transient failures, refused/aborted classification, malformed
  responses, and retry evidence delivery before a terminal error.
- Evidence integration is pending a scope clarification: the existing runtime
  persists explicit completion fields rather than the completion metadata object.
  ModelQueue now exposes a retry callback, but wiring it to the board event store
  requires a small change outside model.py and tests. No such change was made.
- Verification: `python3 -m unittest discover -s tests`
  Result: `Ran 176 tests in 13.589s` / `FAILED (errors=9)`.
  All nine errors are existing HTTP fixtures denied socket binding by this
  sandbox (`PermissionError: [Errno 1] Operation not permitted`); all six new
  scripted tests passed. No live model-server verification was attempted.
- Stage-9 policy, deadlines, and budgets were not changed. Nothing was staged or
  committed. Token usage was not reported by the session tooling.
