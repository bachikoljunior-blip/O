# Work-mode O execution

## Authority and source of truth

The current ChatGPT Work session is the primary development owner. It performs semantic judgment and implementation through the ordinary O Engine. Repository latest `main`, not chat history or unsaved reasoning, is the durable continuation source.

The model is connected to O through the checked-in `continual work-start`, `work-pending`, `work-submit`, `work-resume`, and `work-verify` path. O retains the Run, immutable component requests and responses, invocation journal, fragments, Local Learn results, Episodes, Candidates, memory, evidence, and continuation. `work-verify` audits every persisted request and response digest, binding, output digest, and component contract, including already-consumed invocations. The Work model identity remains unverified unless the platform provides an independently inspectable identity signal.

## Primary execution loop

1. Read `AGENTS.md`, this file, `agi/WORK_MODE_HANDOFF.json`, `agi/WORK_EXECUTION_STATE.json`, `agi/CONTINUATION.json`, and the referenced native O Run from latest `main`.
2. Verify that there is no different fresh writer. A fresh different owner is a hard duplicate-writer stop.
3. Resume the exact pending Work invocation with `continual work-pending` and `continual work-resume`; never recreate a frozen request merely because a process restarted.
4. Execute one falsifiable unit, record objective observations and negative evidence, then evaluate it through O.
5. If the user-level external goal is still unmet, Root immediately selects the next falsifiable unit. A test, commit, PR, CI result, merge, report, checkpoint, or one milestone is not an exit condition.
6. Refresh the primary heartbeat and exact continuation while work is active.

The primary run may stop normally only after the strict independent external production evidence gate has actually passed and the verified evidence has been persisted. If the platform or process forces an end first, persist the exact continuation and mark the lease `interrupted` or `checkpointed`; that is recovery state, not completion.

## Expected-time strategy

`agi/WORK_STRATEGY.json` is the durable, falsifiable strategy contract. Root selects work by expected reduction in defensible elapsed time to real-world AGI under the unchanged strict external gate, probability of producing decision-changing evidence, information value, dependency latency, reuse, and protected-regression risk. O is a replaceable means, not the terminal objective. Root may change, replace, or abandon O when a scoped comparison favors a materially different safe route. Held-out mechanical evidence and genuinely independent production evidence outrank self-evaluation and architecture claims.

When retained observations show that a method is slow, saturated, blocked, non-discriminating, or overrepresented in the recent exploration window, Root changes method or searches heterogeneous information instead of repeating it. A deferral must record an observed reason, the work selected instead, a finite `reevaluate_on` condition, and `reevaluate_by`; every Root cycle checks those conditions. The strategy itself is not presumed correct and must be revised when its measured consequences increase expected completion time. No optimization may weaken the gate, admit self-evaluation as proof, compromise evidence integrity, or create a second writer.

## Durable user requests

`agi/USER_REQUEST_QUEUE.json` records important ambiguity, proposals, or environment, permission, operation, and information that only the user can supply or that would materially shorten expected completion time. `src/agi/user_request_queue.py` validates the queue and provides revision-bound, idempotent insertion. The queue never contains secrets; a credential request asks the user to configure an approved secret channel rather than paste a value.

Every open item identifies safe reversible alternative work and a finite reevaluation condition. An open request does not stop the primary run, release the lease, or excuse failure to explore independent work. Root may notify the user, but it continues the best available non-conflicting work while waiting.

## Single-writer lease

`agi/WORK_EXECUTION_STATE.json` is authoritative. A primary owner created by direct user instruction is distinct from a monitor recovery owner. While its `running` heartbeat is within `stale_after_seconds`, the watchdog must suppress itself.

Malformed state, missing required fields, future-skewed time, an unknown status, generation mismatch, predecessor mismatch, reused fence, or remote readback mismatch fails closed. It never permits a best-effort second writer.

## Monitor as insurance

The scheduled Work monitor is not an ordinary development loop. It reads latest remote `main`, the Work lease, the exact native Run, open PRs, and exact-head checks. It performs no mutation when the primary owner is live.

If the lease is stale, released, interrupted, or checkpointed and the strict external goal is still unmet, the monitor may classify the run as recovery eligible. Eligibility alone is read-only. Recovery mutation is authorized only by the two-phase protocol implemented in `src/agi/work_mode_monitor.py`:

1. state-only expected-blob compare-and-swap with generation exactly `+1`, a unique recovery execution id, predecessor binding, and a new opaque fence;
2. remote readback of that acquisition;
3. a second state-only compare-and-swap recording the acquisition commit/blob and `verified_remote_readback: true`;
4. remote readback of the finalization commit/blob and exact validation by `authorize_work_mode_recovery`.

Only the fenced recovery owner may then resume the native O Run. External effects remain guarded by invocation journals and idempotency records.

## Claim boundary

Work integration, internal tests, native O execution, self-evaluation, repository CI, Candidate promotion, and long uninterrupted operation are internal development evidence only. They do not prove AGI. Until genuinely independent signed production evidence passes every required gate with the required independent evaluator quorum and no unresolved admissible failure or contamination, `agi_claim_supported` remains `false` and development continues.
