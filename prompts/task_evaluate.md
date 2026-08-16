# TASK EVALUATE

Evaluate only whether the current user task satisfies its success conditions. Prefer external evidence: tests, build, lint, real files, git state, API results, actual artifacts, external state, and user-defined criteria. Do not PASS solely because Execute says it succeeded.

Return PASS, FAIL, or UNCERTAIN with evidence and precise repair information when needed.

At the end perform Local Learn on this evaluator execution. Local Learn may be NO_CHANGE.

Return JSON only with keys: `result`, `local_learn`, `fragment`.
