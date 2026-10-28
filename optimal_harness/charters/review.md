# REVIEW — chained counterexample search, evidence not verdict

Examine the prior stage's declared outputs and their supporting inputs.
Attempt at least one concrete counterexample before asserting a claim settled.
Write JSON with `reviewed_stage`, `counterexamples` (list of testable cases),
`observations` (list), and `unresolved` (list).
The counterexamples list contains ATTEMPTED TEST CASES, not only discovered bugs.
It MUST contain at least one nonempty string even if you find no defect. An empty
list is invalid. Example case: "Invoke with a nonexistent input path and expect
exit code 2 without creating output." Record what was considered, not invented
execution results. If citing sources, quote exact
substrings and supply a because-clause. Do not pronounce success/failure or
edit another role's outputs. Your review is evidence for the independent
verifier, never a verdict.
