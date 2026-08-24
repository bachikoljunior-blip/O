# Work-mode O execution

## Authority and source of truth

The current Work session is the primary development owner. In this epoch that owner is the generation-5 ChatGPT Work session identified in `agi/WORK_EXECUTION_STATE.json`: it first acquired the stale lease through the fenced two-phase recovery protocol and was then explicitly designated primary by inbox revision 13. The Claude predecessor is stopped as executor, and its identity-bound orphaned invocations must not be answered by the successor. The active model identity is platform-unverified and must stay recorded as such. Repository latest `main`, not chat history or unsaved reasoning, is the durable continuation source.

The model is connected to O through the checked-in `continual work-start`, `work-pending`, `work-submit`, `work-resume`, and `work-verify` path. O retains the Run, immutable component requests and responses, invocation journal, fragments, Local Learn results, Episodes, Candidates, memory, evidence, and continuation. `work-verify` audits every persisted request and response digest, binding, output digest, and component contract, including already-consumed invocations. The Work model identity remains unverified unless the platform provides an independently inspectable identity signal.

## Primary execution loop

1. Read `AGENTS.md`, this file, `agi/WORK_MODE_HANDOFF.json`, `agi/WORK_EXECUTION_STATE.json`, `agi/CONTINUATION.json`, and the referenced native O Run from latest `main`.
2. Verify that there is no different fresh writer. A fresh different owner is a hard duplicate-writer stop.
3. Resume the exact pending Work invocation with `continual work-pending` and `continual work-resume`; never recreate a frozen request merely because a process restarted.
4. Execute one falsifiable unit, record objective observations and negative evidence, then evaluate it through O.
5. If the user-level external goal is still unmet, Root immediately selects the next falsifiable unit. A test, commit, PR, CI result, merge, report, checkpoint, or one milestone is not an exit condition.
6. Publish validated execution results from an isolated branch through exact-head CI and merge only an unchanged passing head; state-only heartbeat and inbox acknowledgements use expected-blob CAS on `main`.
7. Refresh the primary heartbeat and exact continuation while work is active.

The primary run may stop normally only when the user's actual upper-level objective is truthfully satisfied or the user explicitly stops it. The repository-authored strict independent external production evidence gate remains available as conservative verification machinery, but it is not the user's completion criterion or the monitor-stop condition. If the platform or process forces an end first, persist the exact continuation and mark the lease `interrupted` or `checkpointed`; that is recovery state, not completion.

## Expected-time strategy

`agi/WORK_STRATEGY.json` is the durable, falsifiable strategy contract. Root selects work by expected reduction in defensible elapsed time to real-world AGI, probability of producing decision-changing evidence, information value, dependency latency, reuse, and protected-regression risk. O and any repository-authored gate are replaceable means, not the terminal objective. Root may change, replace, or abandon O when a scoped comparison favors a materially different safe route. Held-out mechanical evidence and genuinely independent production evidence outrank self-evaluation and architecture claims.

When retained observations show that a method is slow, saturated, blocked, non-discriminating, or overrepresented in the recent exploration window, Root changes method or searches heterogeneous information instead of repeating it. A deferral must record an observed reason, the work selected instead, a finite `reevaluate_on` condition, and `reevaluate_by`; every Root cycle checks those conditions. The strategy itself is not presumed correct and must be revised when its measured consequences increase expected completion time. No optimization may lower the user's achievement standard, admit self-evaluation as proof, compromise evidence integrity, or create a second main writer.

## Durable user requests

`agi/USER_REQUEST_QUEUE.json` records important ambiguity, proposals, or environment, permission, operation, and information that only the user can supply or that would materially shorten expected completion time. `src/agi/user_request_queue.py` validates the queue and provides revision-bound, idempotent insertion. The queue never contains secrets; a credential request asks the user to configure an approved secret channel rather than paste a value.

Every open item identifies safe reversible alternative work and a finite reevaluation condition. An open request does not stop the primary run, release the lease, or excuse failure to explore independent work. Root may notify the user, but it continues the best available non-conflicting work while waiting.

## Live user input

`agi/USER_INPUT_INBOX.json` on latest remote `main` is the append-only control plane for user directions that arrive while the primary execution is already alive. It is separate from `agi/USER_REQUEST_QUEUE.json`: the queue carries O-to-user requests, while the inbox carries user-to-O input.

At each safe Root or logical-unit boundary, refresh the inbox from latest remote `main`, compare its revision with the highest acknowledged revision persisted in durable execution state, and ingest any newer active entries. This read does not require a development-writer lease and must not create a second writer. A watchdog restart is not required merely to notice new input. Do not interrupt a frozen semantic invocation mid-call; apply the input at the next safe boundary and preserve exact replay of already-frozen requests.

Inbox entries are directions, constraints, design context, or hypotheses rather than automatic technical truth or AGI evidence. Preserve the user's terminal objective and explicit constraints, but test technical proposals, compare them against alternatives, and reject or revise them when retained evidence favors another route. Persist the highest acknowledged revision and the factual interpretation needed to survive context reset. The current accumulated input includes expected real-world AGI elapsed-time minimization, O replaceability, autonomous strategy/evaluation/exploration/self-improvement/timing, saturation-driven method change, heterogeneous external research search, non-blocking durable user requests, strict claim-gate preservation, falsifiability of user proposals, and recursive situation-dependent Skill-in-Skill context routing.

All future remote appends use the dedicated `append_remote_user_input_inbox`
path in `src/agi/user_input_inbox.py`. The caller adapts its provider to one
read callback returning exact UTF-8 content plus blob SHA and one Contents-API
compare-and-swap callback. The path validates the current JSON and schema,
requires the exact expected revision, assigns only contiguous sequences,
rejects duplicate IDs and secret-bearing fields, performs one expected-blob
CAS, and requires exact content/blob remote readback. Bounded readback retries
may absorb provider propagation delay, but the mutation itself is never
retried. A conflict or exhausted/mismatched readback fails closed for explicit
reconciliation; an ambiguous successful retry is idempotent only when every
requested entry already exists at its exact expected sequence and content.

## O-owned decision context

Inbox revision 15 adds a structural requirement: context known by the outer Work
session must not silently disappear from the semantic context used by O. The
selected architecture is recorded in `agi/CONTEXT_KERNEL_ARCHITECTURE.md`.
O owns the decision-control plane—observation history, source clocks,
provenance, effective constraints, inclusion and exclusion decisions,
freshness, invalidation, immutable decision projections, and context-bound
effect authorization. GitHub, CI, providers, files, and other systems retain
authority for the raw facts they produce. Copying every payload into O is not
the design: it would create competing stale authority, overload, and secret
risk.

The outer session acts as O's observation and effect executor. A fact learned
only in untracked outer context is not allowed to affect an O decision until it
is represented by a canonical source update or an O observation receipt. Every
new Root request binds mandatory lease, inbox, O-owned typed effective
directives, strategy, and native-run source clocks through a deterministic
`DecisionContextManifest`. The typed directive ledger is cross-bound to the
exact inbox bytes and resolves partial supersedes at atom scope; runtime code
does not infer revocation semantics from free text. Missing,
contradictory, or outer-injected mandatory context fails closed. A frozen
pending request keeps its exact manifest for replay; new source revisions apply
at the next safe boundary and create a new request identity.

Mandatory control context is outside optional learned routing. Recursive
situation-dependent routing may select additional context only after objective,
effective user constraints, lease/fence, continuation, source clocks,
contradictions, and safety/revocation context are fixed. Plans and external
effects must eventually bind the manifest that justified them and recheck
critical revocations immediately before dispatch. Until that later stack and
behavioral outcome tests exist, the Root-only slice is incomplete engineering
evidence rather than proof that the context problem is solved.

## Single-writer lease

`agi/WORK_EXECUTION_STATE.json` is authoritative. A primary owner created by direct user instruction is distinct from a monitor recovery owner. While its `running` heartbeat is within `stale_after_seconds`, the watchdog must suppress itself.

Malformed state, missing required fields, future-skewed time, an unknown status, generation mismatch, predecessor mismatch, reused fence, or remote readback mismatch fails closed. It never permits a best-effort second writer.

## Monitor as insurance

The scheduled Work monitor is not an ordinary development loop. It reads latest remote `main`, the Work lease, the exact native Run, open PRs, and exact-head checks. It performs no development mutation when the primary owner is live. It may still observe the append-only user-input control plane so that a later recovery cannot miss input that arrived during a live run; observation alone never authorizes a second development writer.

If the lease is stale, released, interrupted, or checkpointed and the user's upper-level objective is still unmet, the monitor may classify the run as recovery eligible. Eligibility alone is read-only. Recovery mutation is authorized only by the two-phase protocol implemented in `src/agi/work_mode_monitor.py`:

1. state-only expected-blob compare-and-swap with generation exactly `+1`, a unique recovery execution id, predecessor binding, and a new opaque fence;
2. remote readback of that acquisition;
3. a second state-only compare-and-swap recording the acquisition commit/blob and `verified_remote_readback: true`;
4. remote readback of the finalization commit/blob and exact validation by `authorize_work_mode_recovery`.

Only the fenced recovery owner may then resume the native O Run. External effects remain guarded by invocation journals and idempotency records.

## Claim boundary

Work integration, internal tests, native O execution, self-evaluation, repository CI, Candidate promotion, and long uninterrupted operation are internal development evidence only. They do not prove AGI. A truthful AGI claim requires adequate independent real-world evidence and no known decisive contradiction; a repository-authored gate may help test that claim but cannot define the user's completion condition by itself. Until that claim is supportable, `agi_claim_supported` remains `false` and development continues.
