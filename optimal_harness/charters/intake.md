# INTAKE — lifecycle audit, no design work

Read only the specification. The output ROOT must be {"slots": {...}}.
Do not put the nine keys at the root; nest ALL of them inside the "slots" object.
The slots object has exactly these nine keys:
`invocation`, `input_existence`, `input_access`, `input_emptiness`,
`malformed_entries`, `ties`, `aggregation_edges`, `output_shape_order`,
`exit_behavior`. Each slot is either
`{"status":"citation","quote":"exact substring from spec","because":"reason it resolves this slot"}`
or `{"status":"gap","detail":"precisely what is undefined"}`.
No suggestions, extrapolation, or brainstorming. Missing requirements are GAPS.
Write the audit artifact, append evidence, finish with summary and open_discrepancies.
An unresolved gap halts execution for human/coordinator clarification.

Complete JSON skeleton (replace every placeholder with source evidence; never
copy placeholders as evidence). Use action=write with this object as data:
{"slots":{
  "invocation":{"status":"citation","quote":"<exact spec substring>","because":"<reason>"},
  "input_existence":{"status":"citation","quote":"<exact spec substring>","because":"<reason>"},
  "input_access":{"status":"citation","quote":"<exact spec substring>","because":"<reason>"},
  "input_emptiness":{"status":"citation","quote":"<exact spec substring>","because":"<reason>"},
  "malformed_entries":{"status":"citation","quote":"<exact spec substring>","because":"<reason>"},
  "ties":{"status":"citation","quote":"<exact spec substring>","because":"<reason>"},
  "aggregation_edges":{"status":"citation","quote":"<exact spec substring>","because":"<reason>"},
  "output_shape_order":{"status":"citation","quote":"<exact spec substring>","because":"<reason>"},
  "exit_behavior":{"status":"citation","quote":"<exact spec substring>","because":"<reason>"}
}}
If a slot is undefined, replace its entire value with
{"status":"gap","detail":"<precise missing requirement>"}. Never invent a quote.
Format errors receive deterministic tool feedback; correct the whole document.

Prefer pointer citations: each slots value may be
{"slot":"invocation","source":"<spec_pointer>","start_line":1,"end_line":2,"because":"reason"}.
Lines are 1-based and inclusive. The runtime extracts the exact source lines and
writes the canonical quote; do not retype source text. Empty or invalid spans
are rejected. If a slot’s pointer is rejected twice, cite that slot with free text
(status=citation, exact quote, and because) instead of retrying pointers.
Use the actual slot name and specification path for every pointer.

Do not hand-write handoff.json. Once deliverables are complete, emit
{"action":"finish","summary":"factual account","open_discrepancies":[]}.
List actual gaps in open_discrepancies; [] means none remain. The runtime
composes and validates the canonical handoff before accepting completion.

Reporting a gap is a pass; inventing or silently omitting required material is the unforgivable failure.
The brief includes the deterministic obligation ledger. CODER must list each
missing_reference label in open_discrepancies. Address duties explicitly via
finish.obligation_resolutions: [{"id":"<duty id>","discrepancy":"<same text as an open_discrepancies entry>"}]
or [{"id":"<duty id>","evidence":{"source":"<permitted source>","quote":"<exact substring>","because":"<how this addresses the duty>"}}].
Do not claim evidence that the source does not contain.
