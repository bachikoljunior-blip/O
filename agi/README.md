# AGI capability program

The user-level goal is AGI. This directory records falsifiable progress without relabeling an intermediate artifact as completed AGI.

## Current concrete milestone

Version 0.3 provides:

- an installed `agi` package and a six-criterion evidence ledger;
- a conservative claim policy requiring repeated, independent, production-tier evidence;
- a 12-task one-turn development suite and a separate 12-task multi-turn workspace suite;
- explicit tool-failure recovery, persistent memory, learned-procedure reuse, noisy-data handling, and prompt-injection tests;
- a deterministic reference workspace agent that validates the harness but cannot count as AGI evidence;
- repeated campaign execution with per-run artifacts, a ledger, and a claim-evaluation report;
- an OpenAI Responses API adapter that receives goals, visible state, observations, and tool contracts without hidden expected outputs;
- a restart-safe continual runtime able to learn scoped procedure Candidates from episodes.

The current automated verification is 28 unit/integration tests plus compilation, suite validation, both reference runs, and a repeated reference campaign.

## Why this is not yet a verified AGI claim

The repository now tests more than one-shot answers, but it still contains development environments whose task families are public and whose reference agents are benchmark-specific. It does not yet contain repeated independent production evidence across multiple held-out domains for every criterion. `agi_claim_supported` must therefore remain false.

A reference campaign proves that the evaluator and tools behave as specified. It does not prove general intelligence. A real model passing the public development campaign would be stronger capability evidence, but still would not by itself establish AGI.

## Next falsifiable milestones

1. Execute the real-model campaign and preserve all failures, traces, costs, and exact model snapshots.
2. Convert failure clusters into reversible Candidate overlays, compare them against a baseline, and adopt only improvements with no protected-regression failures.
3. Add parameterized and held-out task generators whose exact instances are unavailable during procedure development.
4. Separate task generation, execution, and scoring identities so one actor cannot author both its test and its passing evidence.
5. Add long-horizon repository tasks with sandboxed code execution, checkpoints, and rollback validation.
6. Run continual-learning sequences with before/after regression suites and delayed retention tests.
7. Obtain independent production-tier evaluations in materially different domains and rerun the conservative claim gate.

The evidence gate must be strengthened when false-positive paths are found; it must not be weakened simply to produce a PASS.
