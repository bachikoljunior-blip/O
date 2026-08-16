# CONSOLIDATE EPISODE

Input Experience Fragments and task evidence are the source material. Your output is one logical Task Episode.

Experience Fragment -> Consolidate Episode -> Task Episode.

Do NOT reconstruct missing lost context by guessing. Mark missing fragments/unknowns explicitly. Organize order, deduplicate repeated references, map success conditions to results, integrate Task Evaluate, Candidate pre/post evaluations, trial scope, side effects, environment, artifacts, unresolved items, and searchable metadata.

Distinguish observation, inference, self-report, external evidence, and later user feedback when provenance is available.

At the end perform Local Learn on the consolidation process itself, then return the Episode and fragment.

Return JSON only with keys: `result`, `local_learn`, `fragment`.
