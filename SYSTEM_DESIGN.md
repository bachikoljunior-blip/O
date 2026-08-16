# System design v0.2

## 1. Minimal fixed kernel

Only non-semantic mechanics are fixed in Python:

- atomic text/JSON replacement and append-only JSONL;
- IDs, stable digests, timestamps, and optimistic snapshot revision checks;
- safe repository path enforcement and secret/environment filtering;
- deterministic invocation journals and completed-output reuse;
- loading the already selected component version;
- the mechanical `continue / finished / blocked / cancelled` loop;
- the bootstrap needed to run the current Candidate Evaluator.

Meaningful choices—task interpretation, next work, execution procedure, evaluation, learning, context selection, Candidate applicability, and benchmark design—remain prompt/Candidate controlled.

## 2. Active component pointers

`.continual/system/active-components.json` is read for every semantic invocation. A selected Candidate may override the prompt only for the exact target component and trial scope. The running evaluator and runner are never hot-swapped mid-call or mid-process.

Direct model tool writes to active prompts, `AGENTS.md`, `SYSTEM_DESIGN.md`, workflow files, and active system pointers are blocked. An improvement must be returned as a Candidate and evaluated before use.

## 3. Restart-safe invocation protocol

Before a model call, the runner writes:

`.continual/runs/<run_id>/invocations/<deterministic_id>.json`

States are `started → output_ready → complete` or `failed`.

The ID is a digest of component, selected prompt path, and payload. If `complete` exists on resume, its output is reused. If `output_ready` exists, fragment/Candidate persistence is completed without calling the model again. Repository writes are atomic, network/publish commands are blocked inside the model tool adapter, and external side effects require a separate persisted adapter.

## 4. Execution order

1. `continual start` persists request/snapshot and evaluates runner Candidates at the new-run boundary.
2. ENTRY runs after component-specific Preflight and persists normalized success conditions.
3. A fresh Root chooses exactly one execution unit.
4. Immediately before that unit, only Candidates targeting its component (plus dependencies) are evaluated.
5. Execute creates artifacts/evidence or schedules one child unit with a persisted parent continuation.
6. Task Evaluate returns `PASS`, `FAIL`, or `UNCERTAIN` from objective evidence.
7. Failed/uncertain tasks return to a fresh Root for repair or a new experiment.
8. PASS triggers Episode consolidation.
9. Post-task Learn reviews the current episode plus a compact episode catalog and Candidate index.
10. The run finishes only after the task episode and post-task learning are persisted.

When a caller step budget expires, the snapshot stays `continue` with `checkpoint_reason=step_budget_exhausted`. This is a resumable checkpoint, not a semantic failure.

## 5. Local Learn and episodes

Every semantic component except Learn itself returns `local_learn` before its context is discarded. It may be `NO_CHANGE`, evidence, a hypothesis, or a Candidate proposal. Learn never recursively learns at its own end.

Fragments save purpose, actions, observations, evidence references, failures, unresolved items, environment, and invocation ID—not hidden chain-of-thought. Consolidation must preserve provenance and must not infer missing context.

## 6. Candidate lifecycle

Candidate states are scoped and reversible. Pre-application decisions include applicability, dependency/conflict handling, baseline need, evidence, regressions, rollback, cost, and risk. Post-result decisions can verify, activate for scope, reject for scope, retain, request evidence, or remain uncertain.

Repeated proposals with the same Candidate ID merge evidence and source references. Active-for-scope remains subject to contradictory evidence and downgrade.

## 7. AGI evidence boundary

`src/agi/evaluation.py` separates development progress from claim-grade evidence. A positive AGI claim requires all six criteria and, by default, repeated production-tier successes across multiple tasks, runs, and domains with independent verification and no unresolved production failures.

`src/agi/benchmark.py` is a development harness covering all criteria. Its task-specific reference agent validates harness correctness only. Passing it cannot establish AGI.

This boundary lets the project continue making falsifiable capability advances without fabricating completion.
