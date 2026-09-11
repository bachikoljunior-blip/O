# Continue after a bounded Work Run finishes

Finishing a bounded native Run does not discharge the broader user request or its
retained parents. `WorkSession.start` continues to reject a new Run while an
authoritative Work execution is active. Use the explicit terminal handoff method
only after publishing and verifying the completed Run:

```python
from pathlib import Path
from continual.work_session import WorkSession

session = WorkSession(Path("."), model_identity="chatgpt-work-model-unverified")
result = session.start_continuation("run-published-finished-predecessor")
```

The authoritative `terminal_run_handoff` plan fixes schema version 1, status
`ready`, `continuation_required: true`, a unique `handoff_id`, predecessor and
successor Run IDs, execution ID, lease generation, SHA-256 of the raw fence,
executor binding, model identity, terminal native Learn invocation ID, successor
request reference and SHA-256, and `source_main_sha` with a `source_files` mapping
from repository paths to Git blob SHAs. It must be present in the observed current
main state. The source commit and the exact successful CI head must be ancestors
of fetched `origin/main`.

Publish the finished snapshot, completed Learn journal, Work request and response,
events, Episode, post-task Learn result, every retained parent result, fixed new
request, Candidate index, active component configuration, startup prompts, and
the three handoff runtime files together. The manifest binds those exact source
bytes. The existing causal preflight, mandatory fresh Work authority observation,
and exact-head CI observation are checked again at use time. The predecessor must
have no awaiting native invocation and must have one final Learn completion
followed by one finished event. A runner or wildcard Candidate is rejected by
this initial handoff implementation; its preflight is never silently skipped.

The method records an immutable intent, initializes the successor with the entire
unchanged parent stack and explicit predecessor result paths, and freezes exactly
one Entry request (or the Candidate evaluation immediately before Entry). It does
not consume any response or change the authoritative Work state. Publish that
new native frontier and rebind the authoritative Run before ordinary Work resume.
Keep the same execution lease; this is not a watchdog recovery or a new writer.

Retry uses the same intent and frozen invocation. Missing initial request,
snapshot, or first event can be restored only when no native invocation or orphan
Work request exists and every surviving initialization byte agrees. Conflicting
or ambiguous partial native state fails closed and requires integrity repair.
Fresh observations are still required on retry. An already saved response is
retained without consumption. Historical errors, scoped negative evidence,
parent obligations, and model verification limitations remain unchanged.

The original physical interruption cause remains unknown unless external runtime
evidence establishes it. A terminal-start guard rejection is evidence about that
specific handoff boundary, not a diagnosis of earlier runtime or scheduler stops.
