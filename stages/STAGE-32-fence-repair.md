# Stage 32 — Repair-first write validation (deterministic fence unwrap)

Branch: `codex/stage-32-fence-repair` from main's tip (ec7ab7d).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-32, measured 2026-10-28)

Stage 31's reject-only validation ABORTED the run in cycle 1
(coder_action_format_exhausted:unwritable, zero gate evaluations):
the CODER fence-wraps file content persistently — the board's raw
model output begins ```python followed by the shebang and code — in
all 3 generations, for both wordfreq.py and test_unit.py. Rejection
converted a degraded-but-progressing pipeline into a hard abort.
The fence is a mechanical wrapper, not a semantic defect: it can be
removed deterministically (the campaign precedent is Stage 14's
accept-and-split: repair mechanically, reject only the unrepairable).

## The change (Stage 31's write-validation path)

1. Before compiling a .py write, deterministically UNWRAP a markdown
   fence: if the content's first line is a fence opener (a line whose
   stripped form is ``` or ```<lang>), drop that line; if the
   content's last non-empty line is a bare ``` fence closer, drop it.
   Handle the opener sharing its line with following code by also
   stripping a leading ```<lang> prefix token when it appears at the
   very start of the content. No other content is altered.
2. If unwrapping changed the content, write the UNWRAPPED bytes and
   record a `write_repaired` event (path). Then compile as in
   Stage 31; only content STILL unparseable after unwrapping is
   rejected with the SyntaxError retry reason (Stage 31 behavior,
   budgets unchanged).
3. Enrich `write_rejected` events with the first line of the
   offending content (bounded to 80 chars) so a rejection is
   diagnosable from the board alone.
4. Everything else unchanged: dedup, budgets, cycle semantics.

## Tests

- Fenced .py content (opener+closer lines; opener with language tag;
  opener prefix sharing a line) is unwrapped, written, compiles, and
  fires write_repaired — with the inner content byte-identical apart
  from the fence lines.
- Content that is unparseable even after unwrapping is rejected with
  the enriched event (first line present, ≤80 chars).
- Valid unfenced .py writes are byte-identical to Stage 31 behavior
  (no repair event).
- A file whose legitimate first line merely contains backticks later
  in the line is not mangled (only a line that IS a fence counts).
- Suite fully green (226 existing + new).

## Acceptance

Suite green; coordinator live run: the CODER stage completes cycle 1
with write_repaired events on fenced writes, gate evaluations occur,
and the trajectory is reported against verify-31 (best 11) with
Stage 31's dedup now live-tested.

## Stage 32 implementation

- Added deterministic Python fence unwrapping in the Stage 31 write-validation
  path, including bare/language opener lines and an opener prefix sharing the
  first code line. Inner bytes and trailing blank lines are preserved; storage
  chunks are regenerated from repaired content.
- Added `write_repaired` events with the path. Compilation still rejects invalid
  candidates before publishing bytes; `write_rejected` now includes `first_line`
  bounded to 80 characters from the candidate compiled after repair.
- Added tests for all three opener forms, exact UTF-8/CRLF preservation,
  unchanged valid unfenced content containing later backticks, repair events,
  and bounded rejection diagnostics. Updated existing rejection fixtures to
  remain syntactically invalid after fence repair. No budget, prompt, dedup,
  or cycle-semantics changes.
- Verification: `python3 -m unittest discover -s tests`
  suite result: `Ran 229 tests in 16.114s` / `FAILED (errors=9)`.
  The remaining 220 tests passed, including the Stage 32 coverage. All nine
  errors are existing HTTP tests denied socket binds by this sandbox
  (`PermissionError: [Errno 1] Operation not permitted`); no separate loopback
  or model-server calls were run. Full-suite green requires a coordinator run
  in an environment permitting those test fixtures.
- Token usage: not reported by the runtime.
