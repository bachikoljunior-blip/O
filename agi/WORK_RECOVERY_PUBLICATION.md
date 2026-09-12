# Work recovery publication checks

Authority-only `WORK_EXECUTION_STATE.json` heartbeat, acquisition, and checkpoint
commits must retain the already validated `[skip ci]` commit marker. Such commits
must not include source, directive, native-result, or test changes. Run full
exact-head CI for every substantive publication. The marker prevents the known
state-only CI queue amplification; it never exempts executable work from CI.

Before publishing native progress, verify the complete reviewed tree with
`work-verify --run-id <active_run_id>` and `work-checkpoint-verify`. Include the
referenced execution unit, fragment, and Local Learn files, even when Git ignores
new `.continual/` files. Ordinary CI checks the actually checked-in primary Run
through `tests/test_primary_work_publication.py`; synthetic fixture tests alone
do not establish that a published continuation is complete. If a derived record
was lost, restore it only from an existing frozen output and record any unknown
historical metadata explicitly. Never rerun the semantic invocation to recreate
an already completed output.

This operational note records retained recovery practice. Frozen benchmark inputs remain byte-for-byte unchanged. The authoritative execution state links this note for current and successor recovery owners.

Serialize authoritative state with a consistent recursive key order. When a
native publication updates its frontier while the live owner renews a heartbeat,
merge the reviewed frontier into the newest fenced state; preserve its current
ownership and heartbeat. Publish new journals, fragments, Local Learn records,
snapshot and matching continuation in the same reviewed tree. A frontier awaiting
publication must remain explicitly unverified until exact main readback succeeds.

While that native PR is open, keep its head, PR number and CI status in a
pre-existing dedicated CI-tracking object on main that the incoming branch leaves
unchanged. Defer nearby generic `pending_*` aliases
until after the atomic merge: changing them next to the incoming native frontier
can create a textual conflict even with consistent key ordering. If such a
conflict occurs, restore only those state-only tracking aliases to their prior
values, retaining current authority, the predecessor-consumption hold and the
dedicated PR record. Recheck the actual merge tree. This can preserve an unchanged
passing publication head without rewriting native records or waiving CI.

Keep live CI tracking outside any publication object changed by the incoming
branch. PR661 showed that adding adjacent status and validation fields to that
same object can conflict even when the frontier fields themselves are separate.
When preparing the branch, leave its existing publication-tracking object at the
base value and put live PR/CI observations in a different main-only object. Keep
main's nearby `work_status` and `working_branch` tracking fields unchanged during
that merge window. A heartbeat may still renew the same owner. If a conflict is
already present, preserve its evidence, align only the affected tracking fields,
retain the predecessor-consumption hold, and verify the actual merge tree before
merging the unchanged CI head.

Use the same canonical JSON serializer for state preparation and publication.
A Python-serialized local simulation can have different numeric bytes from the
JavaScript-serialized state CAS; its blob is not the identity of the remote
write. Compare the intended raw bytes after the write and inspect the actual
remote commit's merge tree. The PR661 reconciliation retains that distinction
in `artifacts/g35-pr661-tracking-reconciliation.json`.

PR669 also reproduced a conflict between the incoming `last_action` and an
adjacent main `last_activity_at` update. During pending CI, change only the
dedicated main-only CI tracker and `heartbeat_at`; leave `last_action`,
`last_activity_at`, `updated_at`, generic pending fields and the publication
object unchanged. Populate the publication manifest before taking the branch
base. Keep the incoming tracker and publication object equal to that base, and
preserve its `last_action` through this merge window. Check `git merge-tree`
against the actual latest main after tracking writes, not only the initial base.
The reproduced conflict and its source-preserving correction are retained in
`artifacts/g35-root1-publication-conflict-repair.json`.

After full CI, atomic merge and exact main readback, align current display/action
aliases with the verified frontier when authorizing resume. `active_component`
must describe `exact_continuation.pending_component`, `active_unit_id` and
`current_unit_id` must match its current unit, and `target_component`, `next_tool`
and `next_tool_action` must describe the actual pending operation. Preserve
historical observations under their original records. The native request,
journal, snapshot and `exact_continuation` remain the source of execution truth;
old display text does not authorize replay. This alignment belongs after the
merge, so it cannot recreate the adjacent-field CI conflict.

For native CI observations, project the actual connector response into the
receipt schema before calling the recorder. `workflow_run` has exactly `id`,
`workflow_id`, `name`, `status`, `conclusion` and `head_sha`; every job has exactly
`id`, `name`, `status` and `conclusion`. Preserve the observed values and time.
If schema validation rejects a receipt, inspect the native journal before any
resume retry. Retain the rejected projection and correct only its extra fields;
an already recorded Work observation may be replayed idempotently. Do not weaken
the receipt guard, manufacture a source observation or repeat a completed native
component. The Candidate2 correction records both the rejected shape and the
first actual successful consumption.

Keep essential acquisition requests, immutable versions and readback receipts
in files as work proceeds. A transient orchestration cache is not execution
truth. Recover from exact repository objects and preserved files, then inspect
the native frontier before acting. Large authoritative state files may exceed
connector or shell-output limits: read metadata and Git object bytes without
printing the raw state. For a heartbeat-only renewal, compare the returned
commit's complete bytes with the prior bytes plus the one top-level heartbeat
replacement, and verify the Git blob. Preserve any unobserved physical cause as
unknown. Never infer a stopped native component solely from a missing cache key.
