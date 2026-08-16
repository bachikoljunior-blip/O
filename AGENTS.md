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
13. For a scheduled standalone request whose first line is `agiを作って`, read `agi/STANDALONE_HOURLY_AUTOMATION.md`, `agi/CONTINUATION.json`, `agi/AUTONOMY_STATE.json`, and `agi/HOURLY_EXECUTION_STATE.json` before selecting or executing work.
14. In hourly standalone mode, the highest-performance ChatGPT model selected for that fresh session is the primary reasoning and implementation agent. The repository contains the unfinished AGI architecture, tools, learned skills, Candidates, Episodes, evidence, objective, and durable memory; it is not a separate model that replaces the session's reasoning.
15. Every hourly session starts with zero prior chat/session context. Repository state is durable memory; claims made by a previous session are not evidence unless the repository contains the supporting artifact.
16. Use `agi/HOURLY_EXECUTION_STATE.json` as the single-execution lease and progress record. Never run two hourly sessions concurrently. Finish or safely checkpoint by the policy hard stop before the next hourly boundary.
17. Prefer the highest-performance model available to the ChatGPT automation. The current required target is GPT-5.6 Pro. Do not delegate the central reasoning or implementation to Copilot or a lower-capability fallback unless the user explicitly changes this requirement. Record the actual model used in the execution lease.
18. Every hourly standalone run must end with a user-visible final message in that run's newly created chat, including its time window, actual model, completed changes, PRs and SHAs, successful and failed validation, unmerged or blocked work, exact next action, and truthful AGI claim boundary. This report is required even when the run does no work because of overlap, permissions, failure, or interruption.
19. Finalization order is strict: stop starting work; finish or abandon in-flight mutations safely; inspect the latest main and final CI/PR state; persist continuation and release the lease; then send the chat report. After the lease is marked completed/interrupted or the final report is sent, do not merge, commit, update a PR, or otherwise mutate the repository. Any late result remains pending for the next fresh session to reconcile.
20. The user-visible report must summarize evidence and decisions without revealing hidden chain-of-thought, and it must not relabel intermediate progress as AGI.
