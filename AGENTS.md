# Agent instructions

1. Treat `.continual/runs/`, `.continual/episodes/`, `.continual/candidates/`, and evidence ledgers as persistent truth; chat history is not the source of truth.
2. Keep semantic judgment in active prompts or scoped Candidates, not fixed Python branches.
3. Evaluate a Candidate immediately before the exact component it can affect; never activate a new Candidate globally at creation time.
4. Preserve supporting and contradictory evidence. Active-for-scope is reversible.
5. Perform Local Learn at the end of every semantic execution except Learn itself. Keep Post-task Learn.
6. Do not store hidden chain-of-thought. Persist objectives, actions, important decisions, observations, evidence, failures, unknowns, and reusable findings.
7. Use invocation journals and idempotency records. Do not repeat an external side effect merely because a process restarted.
8. Never read, write, log, or commit secrets. Model subprocesses receive a sanitized environment.
9. Active prompts/system pointers/workflows are protected from direct model edits; propose a Candidate.
10. `NO_CHANGE`, negative benchmark evidence, and `UNCERTAIN` are valid results.
11. Do not claim AGI from architecture, self-report, a task-specific reference agent, or a narrow benchmark. Use `src/agi/evaluation.py` and preserve unmet criteria.
12. A checkpoint is not task abandonment. Resume or create a persisted continuation when the objective remains unmet.
13. For a scheduled standalone request whose first line is `agiを作って`, read `agi/STANDALONE_HOURLY_AUTOMATION.md`, `agi/CONTINUATION.json`, and `agi/HOURLY_EXECUTION_STATE.json` before selecting or executing work.
14. In hourly standalone mode, the newly created ChatGPT session is only the runtime host, tool boundary, and safety/validation supervisor. The repository's unfinished AGI—its persisted objective, Engine, skills, Candidates, Episodes, evidence, and continuation state—is the worker. Do not replace it with an unrelated one-off plan chosen from chat context.
15. Every hourly session starts with zero prior chat/session context. Repository state is durable memory; claims made by a previous session are not evidence unless the repository contains the supporting artifact.
16. Use `agi/HOURLY_EXECUTION_STATE.json` as the single-execution lease and progress record. Never run two hourly sessions concurrently. Finish or safely checkpoint by the policy hard stop before the next hourly boundary.
