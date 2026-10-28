# Ledger summary tool — complete behavioral specification

Invocation: Run `python3 ledger.py INPUT.json OUTPUT.json` with exactly two path arguments; any other argument count is an error.

Input existence: A missing input file is an error; the program must not invent or fetch replacement input.

Input document: The input is a JSON array of transaction objects.

Input fields: Each transaction object has a `category` field (a string) and an `amount_cents` field (an integer).

Input errors: Invalid JSON, a non-array document, a non-object entry, a missing field, or a non-integer `amount_cents` is an error.

Aggregation totals: For each category, `totals` is the sum of `amount_cents` over that category's transactions.

Aggregation counts: For each category, `counts` is the number of that category's transactions.

Refunds: Negative `amount_cents` values are refunds; include them in the sums exactly like positive values.

Empty category: A transaction whose `category` is the empty string is counted under the category name `uncategorized`.

Output keys: Write UTF-8 JSON containing one object with exactly the keys `totals`, `counts`, `grand_total_cents`, and `transaction_count`.

Output totals: `grand_total_cents` is the sum of `amount_cents` over all transactions and `transaction_count` is the number of transactions.

Output order: Category keys inside `totals` and `counts` appear in ascending Unicode code-point order.

Ties: Two categories may have equal totals; no tie-breaking applies because each category is reported independently.

Empty input: The empty input array `[]` is valid and produces `totals` `{}`, `counts` `{}`, `grand_total_cents` 0, and `transaction_count` 0.

Fees: Apply the processing-fee schedule in Appendix B of this specification to each category total before writing the output.

Missing references: If this specification references material that is not present in it, do not invent the missing material; implement all behavior that is defined, and record the missing reference in your handoff's open_discrepancies.

Exit behavior: Return exit code 0 on success; on any error return exit code 2, print a diagnostic to stderr, and do not create or replace the output file.
