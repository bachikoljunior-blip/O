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
13. For a scheduled request whose first line is `agiを作って`, read `agi/STANDALONE_HOURLY_AUTOMATION.md`, `agi/REPORTING_TARGET.json`, `agi/CONTINUATION.json`, `agi/AUTONOMY_STATE.json`, and `agi/HOURLY_EXECUTION_STATE.json` before selecting or executing work.
14. In hourly mode, the model actually running in the automation/task chat is the primary reasoning and implementation agent. The repository contains the unfinished AGI architecture, tools, learned skills, Candidates, Episodes, evidence, objective, and durable memory; it is not a separate model that replaces the session's reasoning.
15. The same task chat may be reused across hourly executions, but every run must perform a logical context reset. Prior chat messages, prior final answers, hidden reasoning, unsaved plans, and unpersisted tool results must not be used as work state. Repository latest `main` is the only continuation source.
16. Use `agi/HOURLY_EXECUTION_STATE.json` as the single-execution lease and progress record. Never run two hourly executions concurrently. Finish or safely checkpoint by the policy hard stop before the next hourly boundary.
17. Prefer the highest-performance model available to the automation, but do not block AGI development solely because the actual model is not GPT-5.6 Pro. Record `required_model`, `actual_model`, whether the actual model was platform-verified, and the verification source. Never claim a model identity that cannot be externally verified.
18. The primary destination for every hourly report is the same automation/task chat. Reporting must not turn prior chat history into continuation state; only repository latest `main` may provide durable continuation.
19. If direct report delivery is unavailable, persist the complete factual report under `agi/reports/` and update `agi/LATEST_HOURLY_REPORT.json` before releasing the lease. Never claim delivery without platform confirmation.
20. Every report must include its time window, model verification state, completed changes, PRs and SHAs, successful and failed validation, unmerged or blocked work, exact next action, and truthful AGI claim boundary. This is required even when the run does no work because of overlap, permissions, failure, or interruption.
21. Finalization order is strict: stop starting work; finish or abandon in-flight mutations safely; inspect latest main and final CI/PR state; persist continuation and the exact report artifact; release the lease; deliver the final report. After the lease is marked completed/interrupted, do not merge, commit, update a PR, or otherwise mutate the repository. Late results remain pending for the next repository-only-context run to reconcile.
22. User-visible reports must summarize evidence and decisions without revealing hidden chain-of-thought, and must not relabel intermediate progress as AGI.
