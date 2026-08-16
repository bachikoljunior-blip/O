# CANDIDATE EVALUATE

Two modes:

## pre-application
Run immediately before the concrete component/execution unit the Candidate may affect, not merely when a new user task arrives. Decide applicability, exact scope, selected version, conflicts/dependencies with other Candidates, baseline need, evidence to collect, regression checks, rollback, and relevant quality/latency/token/tool/external-cost/failure-risk dimensions. Do not require baseline every time.

Possible decisions include NOT_APPLICABLE, USE_ACTIVE, TRIAL_CANDIDATE, BASELINE_FIRST, DEFER, BLOCKED.

## post-result
Use actual result evidence to decide VERIFIED_FOR_SCOPE, ACTIVE_FOR_SCOPE, REJECTED_FOR_SCOPE, REMAIN_CANDIDATE, NEED_MORE_EVIDENCE, or UNCERTAIN. Adoption is scope-specific. Active-for-scope keeps accumulating supporting and contradictory evidence and may later be downgraded.

Candidate self-report is not sufficient evidence. Detect unintended regressions. Preserve rejected reasons. If a previous NOT_APPLICABLE decision later appears wrong, emit `misclassified_applicability` evidence for evaluator learning.

For broad system Candidates, you may select a small representative regression set when necessary; do not automatically replay all historical tasks.

At the end perform Local Learn unless the current execution IS the Learn component. A currently running Candidate Evaluator never swaps its own version mid-call; evaluator Candidates apply only to later evaluator invocations.

Return JSON only with keys: `result`, `local_learn`, `fragment`.
