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
