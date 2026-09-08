# O development self-application

## Purpose

Repository development is itself an O task. The model running in the current task chat may perform
the semantic execution and implementation work, while the repository remains the durable source of
truth. Each completed or checkpointed development unit is converted into native O state:

- `.continual/runs/<run-id>/` for request, snapshot, events, fragments, local learning, and invocation journal;
- `.continual/episodes/<episode-id>/` for the consolidated outcome and relations;
- `.continual/candidates/<candidate-id>/` for reusable scoped changes that remain inactive until the
  ordinary pre-application and regression gates accept them;
- `.continual/evidence/self-application/<evidence-id>.json` for internal development evidence.

The adapter is `continual.self_application.record_self_application`. The CLI entry point is:

```bash
continual --root . self-apply --record path/to/self-application.json
```

The operation is idempotent by `execution_id`, `run_id`, and a stable payload digest. Replaying the
same record returns the existing result. Reusing an id with different content fails closed.

## What to persist

Persist the objective, actions, important decisions, observations, artifacts, validation, failures,
unknowns, claims, unresolved work, model verification state, repository/PR/CI references, and an
optional reusable Candidate. Do not persist hidden chain-of-thought, private scratch work, raw system
prompts, credentials, authorization material, cookies, or secret-like strings.

A self-application Candidate is never activated by recording it. It starts as `candidate`, is scoped,
and has `global: untested`. The normal exact-scope Candidate evaluator and protected regression gate
remain authoritative.

## Context policy

Physical fresh context is not a fixed requirement for ordinary repository development. Use the
least disruptive safe boundary:

- `development`: reuse the current context when there is no evidence of context accumulation,
  stale state, duplicate search, or evaluation contamination;
- `development` with one of those risks: persist required state to latest main and perform a logical
  reset before a result may pass;
- `retention_validation` and `transfer_validation`: use a fresh execution;
- `independent_evaluation`: use a fresh independent execution.

A `PASS` record is rejected unless its selected reset/fresh/independent boundary was actually used.
`FAIL` and `UNCERTAIN` records remain persistable so negative evidence is not lost.

When a better execution environment or a fresh component able to invoke the same model becomes
available, treat migration as a scoped Candidate. Compare expected completion time, capability,
reliability, contamination control, and protected regressions. Switch automatically only when the
comparison supports a retained improvement; preserve the losing result and rollback path.

## Claim boundary

All self-application records are hard-coded as `internal_development` and
`admissible_for_agi_claim: false`. They can guide learning and document retained progress, but cannot
establish evaluator independence, production isolation, or AGI. The strict external production
evidence gate remains unchanged.
