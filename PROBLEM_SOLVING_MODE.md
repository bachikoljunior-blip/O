# Transient problem-solving mode

## Contract

O remains an ordinary optimizer by default. A concrete problem starts one explicit,
objective-neutral problem-solving session. The optimizer snapshots its normal role,
enters problem-solving mode for that session, and restores the exact snapshot only
after verified root completion or explicit abandonment.

This is **not** a permanent parallel control plane, a second primary writer, an AGI
objective, or an always-on daemon. Parallel ownership exists only inside the active
session and is closed when that session terminates.

## Complete recursive loop

`continual.problem_solving` implements the durable control loop adapted from J:

1. persist the concrete root problem, success criteria, normal-role snapshot, and
   transient mode switch;
2. forecast the active problem count;
3. select an unresolved leaf, or replace an over-wide local split with a transversal
   integration when the forecast would be exceeded;
4. audit existing-world solutions for the selected leaf and every unaudited ancestor;
5. attempt a direct solution before decomposition;
6. evaluate fail-closed: a candidate solves a node only when evaluation is explicitly
   verified and every required criterion is true;
7. decompose only after direct resolution fails;
8. integrate solved children, then promote verified results through parent nodes and
   finally the root;
9. rewrite and version the problem tree while retaining replaced proposals in history;
10. optionally require verified `publish` and `merge` receipts;
11. restore the prior optimizer role and clear the active-session pointer.

The observation phases are `forecast`, `select_leaf`, and
`existing_solution_audit`. Exclusive phases are `attempt_solution`, `decompose`,
`evaluate`, `integrate_children`, `solve_parent`, `solve_root`,
`update_problem_tree`, `publish`, and `merge`.

## Persistence and replay

A controller root contains:

- `control.json` — the projected normal/problem-solving mode and active session;
- `events/<session>.jsonl` — append-only, sequence-numbered, SHA-256 hash-chained
  transitions containing the complete post-transition state;
- `sessions/<session>.json` — a repairable projection rebuilt from the event log.

The event log is authoritative. Recovery verifies every predecessor and event digest,
then deterministically replays every persisted phase admission against the exact
historical session state. It checks the phase/mode, session and node identity, canonical
paths, pre-transition state digest, coordinator authority or exact claim snapshot,
claim freshness, subtree ownership, and reserved-path coverage. A record can therefore
have a valid hash chain and still fail closed when its admission semantics were altered.
Recovery reconstructs the latest state, repairs a missing or damaged projection, and
resumes an interrupted session without treating chat history as state. An interrupted
session remains in problem-solving mode; it is not silently reported as complete and
the normal role is not restored early.

Role entry and restoration use deterministic operation IDs. A concrete
`OptimizerRoleAdapter` must treat them idempotently so a process restart cannot repeat
an external side effect.

## Session-scoped parallel work

`claim_work` reserves a problem subtree, hierarchical scope, and repository paths for
one worker. Fresh claims reject overlapping nodes, scopes, or paths. `heartbeat_claim`
persists liveness; before a new claim or heartbeat, expired active claims are durably
marked `stale`, allowing a later worker to take over without treating an old owner as
live. Observation may cross claims, while a parallel exclusive phase must present the
matching fresh claim. Claims have no authority outside their session and are closed
automatically on completion or abandonment.

There is no coordination-only permanent branch and no global claim registry in this
mechanism.

## Minimal use

```python
from pathlib import Path
from continual.problem_solving import (
    Evaluation,
    ExistingSolutionAudit,
    Forecast,
    ProblemSolvingController,
    ProblemSolvingHooks,
    ProblemSpec,
    SolutionCandidate,
)

class Hooks(ProblemSolvingHooks):
    def forecast(self, session, root):
        return Forecast(1, "direct root attempt")

    def audit_existing_solution(self, session, node, unaudited_node_ids):
        return ExistingSolutionAudit(tuple(unaudited_node_ids))

    def attempt_solution(self, session, node, audit):
        return SolutionCandidate("candidate result", artifact={"answer": 42})

    def evaluate(self, session, node, candidate):
        return Evaluation(True, {criterion: True for criterion in node["success_criteria"]})

controller = ProblemSolvingController(Path(".continual/problem-solving"))
session = controller.start(
    ProblemSpec("root", "Solve the supplied problem", ("correct", "complete"))
)
state = session.run(Hooks())
assert state["status"] == "completed"
```

Semantic judgment belongs in hooks, prompts, tools, or scoped Candidates. The Python
state machine does not hard-code what a correct domain solution is; it enforces the
order, evidence, ownership, recovery, and fail-closed completion boundary.

## Recovery and termination

- `ProblemSolvingController.recover()` resumes the only unfinished session and repairs
  its projection from valid evidence.
- More than one unfinished session fails closed for manual repair.
- `session.checkpoint(...)` persists a durable non-terminal checkpoint.
- `session.abandon(reason)` is the explicit non-success exit; it closes claims and
  restores the prior optimizer role.
- Starting a second session while one is unfinished is rejected.
