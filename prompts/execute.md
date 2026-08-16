# EXECUTE

Execute the supplied execution unit using the best available general reasoning and tools. If no specialist Skill exists, do not invent a runtime framework; use the model's general capability.

Honor selected Candidate version/scope and collect the evidence requested by Preflight. For external side effects, require a persisted idempotency/side-effect record before the effect and verify external state afterward.

If a child Skill is necessary, return a continuation plus a child execution-unit request instead of depending on nested-context depth.

At the end, before losing this context, perform Local Learn from what was directly observed. Preserve reusable findings and failures without storing hidden chain-of-thought or giant raw logs.

Return JSON only with keys: `result`, `local_learn`, `fragment`.
