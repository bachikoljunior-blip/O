# AGI capability program

The user-level goal is AGI. This directory records falsifiable progress without relabeling an intermediate artifact as completed AGI.

## Current concrete milestone

Version 0.2 provides:

- an installed `agi` package instead of an unimportable repository-root module;
- a six-criterion evidence ledger;
- a conservative claim policy requiring repeated, independent, production-tier evidence;
- a deterministic development suite with at least two tasks per criterion;
- an explicit task-specific reference adapter that validates the harness but cannot count as AGI evidence;
- a restart-safe continual runtime able to learn scoped procedure Candidates from episodes.

## Why this is not yet a verified AGI claim

The repository currently contains architecture and development-test evidence. It does not yet contain repeated independent production evidence across multiple domains and runs for every criterion. `agi_claim_supported` must therefore remain false until that evidence exists.

## Next falsifiable milestones

1. Add a real model/agent adapter that runs the core suite without task-ID-specific handlers.
2. Add held-out suites whose task contents are unavailable during procedure development.
3. Test transfer to new tools/environments and long-horizon recovery from injected failures.
4. Run continual-learning sequences with explicit regression suites before and after learning.
5. Trial self-improvement Candidates against baselines and record reversibility/regressions.
6. Collect independent production-tier evaluations in multiple domains and rerun the conservative claim gate.

The evidence gate must be strengthened when false-positive paths are found; it must not be weakened simply to produce a PASS.
