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

While that native PR is open, keep its head, PR number and CI status in the
dedicated publication object on main. Defer nearby generic `pending_*` aliases
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
