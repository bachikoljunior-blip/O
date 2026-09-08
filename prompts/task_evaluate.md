# TASK EVALUATE

Evaluate whether the original user task—not merely the most recent unit—satisfies its explicit success conditions. Prefer tests, builds, lint, real files, git state, API results, benchmark reports, independently checkable artifacts, and user-defined acceptance criteria. Self-report and architectural sophistication are not sufficient evidence.

Return `PASS`, `FAIL`, or `UNCERTAIN`, with evidence provenance and precise repair information. For AGI claims, use the evidence gate in `src/agi/evaluation.py`; a development benchmark pass alone cannot produce PASS for the full AGI goal.

At the end perform Local Learn on this evaluator execution.

Return one JSON object only with keys `result`, `local_learn`, and `fragment`.
