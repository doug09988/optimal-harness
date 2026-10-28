# Word frequency CLI — complete behavioral specification

Invocation: Run `python3 wordfreq.py INPUT.json OUTPUT.json` with exactly two path arguments; any other argument count is an error.

Input existence: A missing input file is an error; the program must not invent or fetch replacement input.

Input access: An unreadable input file is an error.

Input emptiness: The empty input array `[]` is valid and produces the empty output array `[]`.

Malformed entries: Input must be a JSON array of strings; invalid JSON, non-arrays, and any non-string item are errors.

Ties: Output is sorted by the original string in ascending Unicode code-point order, never by frequency.

Aggregation edges: Count repeated strings exactly, with case sensitivity; `A` and `a` are different keys.

Output shape/order: Write UTF-8 JSON containing an array of objects with exactly `word` and `count` keys, one object per distinct word, in the specified sort order.

Exit behavior: Return exit code 0 on success; on any error return exit code 2, print a diagnostic to stderr, and do not create or replace the output file.
