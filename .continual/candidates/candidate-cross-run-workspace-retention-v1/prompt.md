# Scoped Candidate: live cross-run workspace retention

Use this Candidate only for the exact `agi/live-cross-run-retention-evaluation` scope after the ordinary workspace campaign is stable enough to make retention evidence meaningful.

Run the bounded cross-run retention harness with a fresh genuine-model workspace agent instance. Transfer only the explicitly persisted workspace memory/procedure state from acquisition into the fresh instance; do not depend on private conversation state or hidden reasoning. The fresh instance must recall `labels.azure`, reuse the retained `sorting` procedure, pass the protected transfer and robustness probes, avoid successful `remember`/`adopt_procedure` calls during the retention probe, and leave the transferred state unchanged.

Treat any malformed response, failed task, re-learning/re-adoption event, changed persistent state, or protected regression as negative evidence. Do not weaken task expectations, parsing, scoring, step budgets, or the external AGI claim gate. A passing live result remains internal development evidence unless an independent production evaluator separately satisfies the repository's strict external evidence policy.
