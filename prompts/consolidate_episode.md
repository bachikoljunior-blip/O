# CONSOLIDATE EPISODE

Consolidate the supplied Experience Fragments and artifact references into one logical Task Episode. Do not invent missing context. Mark unknowns explicitly.

Preserve sequence, success-condition mapping, evaluator verdicts, Candidate pre/post decisions, side-effect states, environment, objective evidence, failures, repairs, unresolved end goals, and searchable metadata. Distinguish observation, inference, model self-report, external evidence, and later user feedback.

At the end perform Local Learn on consolidation quality.

Return one JSON object only with keys `result`, `local_learn`, and `fragment`.
