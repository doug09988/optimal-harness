# Stage 14 — Accept-and-split: oversized writes are stored, not denied

Branch: `codex/stage-14-accept-split` from the tip of `codex/stage-13-raw-writes`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence (verify-15, 2026-10-28)

Stage 13's raw writes work — the model USES the raw form. CODER still failed all
3 generations with `coder_action_format_exhausted`, and the denial records show
why: the model's raw payloads for `wordfreq.py` were denied as **oversized**.
It writes the whole small file in one raw payload; the per-chunk cap rejects it;
the model retries the same shape; three denials exhaust the attempt. (Same
mechanics killed QA in verify-13 under the old protocol: 9 size denials.)

The per-chunk cap exists to bound validation/storage work per action — a
runtime-internal concern. Denying a complete, well-formed payload to enforce it
burns a full multi-minute generation teaching a sizing preference the model
does not learn in-run. Per this codebase's winning pattern (runtime owns
mechanics, model owns content), the runtime should absorb the sizing.

## The change

1. When a write payload (raw OR JSON form) exceeds the per-chunk cap, the
   runtime ACCEPTS it and internally splits the content into cap-sized chunks,
   appended in order as if the model had sent them as a chunk sequence. The
   action's `more` flag is honored as before: an accepted oversized payload
   with `more: false` completes the file; with `more: true` the file stays
   open for continuation.
2. A hard TOTAL per-file ceiling (keep the existing one if present; otherwise
   set it at 16x the per-chunk cap) remains a true denial — that is the safety
   bound, and its denial text says the file's total size is the problem.
3. Events/validation record the split (e.g. an event noting
   `oversized_accepted_split` with the chunk count), so the evidence trail
   shows what the runtime absorbed.
4. Everything else identical: path confinement, write scoping, checkpointing,
   mixed raw/JSON sequences, the JSON form, driven stages untouched.

Out of scope: raising/removing the per-chunk cap as a storage unit (it stays —
it just stops being a model-facing denial); budgets; stage-9 policy; charters
beyond removing now-false "use smaller chunks" denial wording (update denial
templates so size wording matches the new behavior).

## Tests

- A single raw write larger than the per-chunk cap produces the byte-exact
  file and records the split event.
- A single JSON write over the cap likewise round-trips.
- A payload over the TOTAL ceiling is still denied, with the total-size
  wording.
- `more: true` oversized chunk + a normal continuation chunk assemble in
  order.
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline.

## Stage 14 implementation

The action layer now accepts valid raw and JSON writes/appends above the
per-chunk cap and stores their content as ordered, UTF-8-aligned chunks within
that cap. The action's `more` flag still controls completion, and successful
split writes emit `oversized_accepted_split` with path, byte count, chunk count,
and chunk limit. The existing 131072-byte total file ceiling is checked before
any storage chunk is written, including existing bytes for append; its denial
explicitly identifies total file size. Scope checks and final checkpoints still
use the existing broker and lease-fenced commit. Driven stages, budgets, and
stage-9 policy are unchanged.

Removed obsolete smaller-chunk denial guidance in action feedback and the three
writer charters. Tests cover raw/JSON byte-exact round trips, actual bounded
storage writes and split event counts, oversized `more=true` with mixed-form
continuation, total-size denial without partial writes, and cumulative append
limits. Older size-rejection tests now exercise invalid schemas or oversized
non-write actions.

Verification: `python3 -m unittest discover -s tests`

Suite result: `Ran 132 tests in 10.944s` — `FAILED (errors=9)`.
All remaining 123 tests passed, including Stage 14 tests. The nine existing HTTP
tests fail at localhost server binding with `PermissionError: [Errno 1]
Operation not permitted` in this sandbox, before making HTTP requests. No
separate loopback or model-server verification was attempted. `git diff --check`
passed. Nothing staged or committed.

Token usage: not reported by the runtime for this implementation session.
