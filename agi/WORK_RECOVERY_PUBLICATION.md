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
