# AGI capability program

The user-level goal is AGI. This directory records falsifiable progress without relabeling an intermediate artifact as completed AGI.

## Current concrete milestone

Version 0.4 provides:

- an installed `agi` package and a six-criterion evidence ledger;
- a conservative claim policy requiring repeated, independent, production-tier evidence;
- public one-turn and multi-turn workspace development suites;
- repeated campaign execution with persistent artifacts and claim evaluation;
- a runtime-generated 12-task held-out suite spanning all six criteria;
- sealed expected values: the executor receives a redacted task whose `expected` field is always `None`;
- distinct generator, executor, and scorer identities enforced by the harness;
- secret-seed handling through `AGI_HELDOUT_SEED`; reports persist only a one-way seed commitment, never the seed itself;
- opaque per-task IDs and per-task commitments so exact generated instances can be audited without exposing answers during execution;
- a public-seed algorithmic reference that validates generation, redaction, scoring, persistence, and role separation but cannot count as AGI evidence;
- a restart-safe continual runtime able to learn scoped procedure Candidates from episodes.

Normal CI validates the public development harness and the public-seed held-out reference. A production held-out run must use a fresh secret seed supplied outside the repository.

## Why this is not yet a verified AGI claim

Held-out generation closes an important false-positive path, but the task generator families and deterministic scorer are still authored in this repository. A single real-model held-out pass would therefore be stronger evidence, not proof of AGI. The repository still lacks repeated independent production-tier evaluation across materially different held-out domains and long-horizon environments.

`agi_claim_supported` must remain false until the evidence policy is actually satisfied. Reference agents and public seeds validate the harness only.

## Next falsifiable milestones

1. Execute real-model public and secret-seeded campaigns and preserve failures, traces, costs, model snapshots, commitments, and exact environment metadata.
2. Convert failure clusters into reversible Candidate overlays, compare them against baselines, and adopt only improvements with protected-regression checks.
3. Move held-out generation and scoring behind an independent evaluation service or separately controlled identity so repository code cannot author and certify the same evidence.
4. Add long-horizon repository tasks with sandboxed code execution, checkpoints, rollback validation, and adversarial tool failures.
5. Run continual-learning sequences with before/after regression suites and delayed retention tests.
6. Add materially different held-out domains and independently authored task generators.
7. Obtain repeated independent production-tier evaluations and rerun the conservative claim gate.

The evidence gate must be strengthened when false-positive paths are found; it must not be weakened simply to produce a PASS.
