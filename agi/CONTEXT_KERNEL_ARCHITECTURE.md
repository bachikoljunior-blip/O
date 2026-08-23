# O Context Kernel architecture

## Decision

O should own the **decision-control plane**, not a second copy of every raw fact.
GitHub, CI, providers, files, and other systems remain authoritative for the
facts they produce. O owns the append-only observation history, source clocks,
provenance, effective user constraints, inclusion and exclusion decisions,
freshness and invalidation rules, immutable per-decision projections, and the
resulting action authorization. The outer Work session executes observations
and effects requested by O and returns receipts; untracked outer memory is not
decision authority.

This is the best interpretation of inbox revision 15. It places context
management, retention, judgment, and the work loop under O control without
turning O into a stale duplicate of the outside world.

## Why the current structure loses context

1. Work requests freeze prompt, payload, model, and executor, but historically
   did not record which authoritative sources were consulted, omitted, stale,
   or contradictory.
2. User input can arrive in the repository inbox after Entry. Unless the outer
   session manually narrates it at a later Root, it is absent from O's semantic
   request even though the outer session knows it.
3. GitHub, CI, lease, continuation, strategy, chat, and native Run records move
   on different clocks. A single persisted snapshot can be replay-correct and
   still be decision-stale.
4. Recursive context routing is optional-selection machinery. It cannot safely
   decide whether to omit lease identity, current user authority, revocations,
   or the terminal objective.
5. Information being present is not equivalent to controlling an action.
   Effect authorization must be bound to the context and source revisions that
   justified it, then critical revocations must be rechecked at dispatch.

## Alternatives considered

| Structure | Benefit | Decisive problem | Decision |
|---|---|---|---|
| Copy every raw source into O | Simple mental model | Duplicate authority, silent staleness, context overload, secret exposure, weak replay economics | Reject |
| Outer session or RAG builds prompts | Low engine change | The outside chooser can omit exactly the fact O needed; selection is not durable O state | Reject as authority |
| O Context Kernel over authoritative references and receipts | O owns every decision projection while sources retain raw authority; replay and invalidation are explicit | Requires source schemas, receipts, and behavioral testing | Adopt incrementally |
| Separate context service | Can scale storage/retrieval | Becomes another hidden decision authority unless O owns its contract and consumes immutable receipts | Allow only behind O's interface |

## Target model

The durable kernel has four layers:

1. **Context Event Ledger** — append-only observations, user-input events,
   source updates, contradictions, invalidations, and effect receipts.
2. **Source Registry and clocks** — authoritative locator, content/version
   identity, observation time, freshness policy, dependencies, and revocation
   semantics for every source kind.
3. **DecisionContextManifest** — an immutable projection for one semantic
   invocation: mandatory control context, recursively routed optional context,
   included and excluded sources with reasons, contradictions and unknowns,
   dependency bindings, and a canonical digest.
4. **Context-bound effects** — plans, dispatch authorization, idempotency keys,
   and receipts bind the manifest and source clocks that justified them.

Mandatory L0 control context is never delegated to optional retrieval:

- terminal objective and effective user constraints;
- current execution owner, generation, fence digest, and completion authority;
- inbox cursor and unresolved user-input revisions;
- native Run/unit/phase and continuation identity;
- source catalog, contradictions, and freshness/invalidation status;
- safety and effect-revocation constraints.

Optional L1+ content can use recursive situation-dependent routing. The
manifest records both the selected branches and exclusions so omission becomes
observable and falsifiable.

## First vertical slice: Root source manifest

`src/continual/context_kernel.py` initially constructed a Root-only manifest from the
checked-in Work lease, user-input inbox, strategy, and native Run snapshot.
Once any AGI control source exists, all mandatory sources must exist and agree.
The bridge refuses an outer-provided competing manifest. It freezes O's
manifest into the immutable Root payload and request digest.

The slice deliberately does not copy all raw files. It projects current
authority, the inbox catalog and latest direction, strategy objective/rules,
and native continuation while recording omitted fields and reasons. A frozen
pending request never changes; a source-clock change creates a new manifest and
request identity at the next semantic boundary.

This slice improves omission visibility and replay binding.

## Second vertical slice: typed effective directives

`agi/USER_INPUT_INBOX.json` remains the authoritative append-only record of
what the user supplied. `agi/USER_DIRECTIVE_EVENTS.json` is O's reviewed,
source-bound interpretation ledger. Each typed atom records an exact source
entry digest, directive indices, a policy slot, cardinality, value, precedence,
and atom-level supersede targets. `src/continual/effective_directives.py`
validates that every active source directive is covered, rejects unknown or
changed source bytes, detects supersede cycles and exclusive-slot conflicts,
and reconciles critical ownership, publication, completion, and context
authority with the current lease and strategy.

This split is deliberate: runtime code does not infer partial supersede
semantics from prose, and the interpretation ledger cannot silently become raw
user authority because every atom is cross-bound to the inbox. The compiled
projection contains effective atoms, superseded atom ids, and a stable policy
digest rather than copying the raw directive corpus into each Root. The
projection is a mandatory manifest source, so an inbox change without a
matching reviewed interpretation fails closed at the next Root while an
already frozen request remains replay-stable.

This slice still does **not** prove useful behavioral improvement, guarantee
remote-main freshness, create external observation receipts, or protect effects
at dispatch. Those remain explicit negative evidence.

## Third vertical slice: O-requested external observations

`src/continual/context_observations.py` separates read-only observation from
effect authorization. O first freezes a bounded observation request under the
current native Execute journal. The outer executor may then return only the
selected public fields, exact immutable source version, status, unknowns, and
evidence class. The receipt is immutable and cross-bound to the Work request,
executor, model, GitHub repository/path/commit, field set, and freshness rule.

`agi/CONTEXT_OBSERVATION_LEDGER.json` contains only projections derived from
verified request/receipt pairs. The Context Kernel re-verifies those pairs
before admitting the ledger to a later Root manifest. Unsolicited outer facts,
changed request or source identity, tampered results, conflicting replay,
future skew, and stale max-age receipts fail closed. An observation bound to an
immutable commit remains a historical source-version fact; it is not silently
promoted to "latest main."

The first checked-in receipt is one connected-GitHub read of the generation-6
Work state at an exact commit. Its evidence class is explicitly
`operator_connector_readback`: this demonstrates the O request-to-connector-
receipt path, not independent attestation, useful behavioral improvement, or
complete external-source coverage.

## Fourth vertical slice: semantic-component manifests

`build_decision_context` constructs the same mandatory, source-clock-bound
projection for Root, Execute, Task Evaluate, Consolidate Episode, and Learn.
The component name is part of the manifest digest, payload digest, Work request
digest, and invocation identity. `build_root_decision_context` remains as a
compatibility wrapper.

`WorkModelClient` rejects an outer-provided `decision_context` at every covered
semantic boundary. Newly minted requests in an enabled Context Kernel must
build their manifest from the durable native snapshot and all mandatory
sources. Replay remains journal-authoritative: a pending request keeps its
original manifest bytes after a source-clock advance, while the next request
gets a different identity. Frozen historical requests without a manifest are
not retroactively rewritten.

This slice establishes uniform internal provenance and replay binding. It does
not yet recheck critical revocations immediately before an external effect,
cover Entry or Candidate Evaluate, demonstrate better task behavior, or provide
independent production evidence.

## Migration sequence

1. Root-only mandatory manifest, audit and fail-closed validation. **Implemented.**
2. Typed inbox-to-O events and an effective-directive compiler that handles
   partial supersedes without deleting unaffected constraints. **Implemented
   for the current revision-15 control source; exact-head publication and
   broader behavioral evidence remain required.**
3. Observation activities and receipts for GitHub, CI, providers, tools, and
   external research; no outside fact affects judgment before ingestion.
   **Implemented for one exact public GitHub file read; other source kinds and
   behavioral evidence remain pending.**
4. Manifests for Execute, Task Evaluate, Consolidate, and Learn. **Implemented
   locally for all five covered semantic components; exact-head publication
   evidence remains pending.**
5. Bind plan and effect authorization to manifests; recheck critical source
   clocks and revocations immediately before dispatch.
6. Route optional context recursively and learn routing only from held-out
   behavioral evidence, with mandatory context outside that learner's control.
7. Compact the event ledger into reproducible projections while retaining the
   events and digests needed for audit and reconstruction.

## Falsification criteria

The architecture fails or needs revision if any of these occur:

- an active user constraint can be omitted from a new semantic request without a recorded
  source or exclusion decision;
- a changed source silently reuses a decision manifest;
- a frozen request changes during replay;
- missing or contradictory mandatory sources do not fail closed;
- a stale authorization can dispatch after a critical revocation;
- manifest overhead materially worsens outcomes or latency without reducing
  omission/staleness failures;
- tests show improved recall or explanation but no useful behavioral/outcome
  change.

Architecture and internal tests are engineering evidence only. They do not
establish AGI or completion of the user's upper-level objective.
