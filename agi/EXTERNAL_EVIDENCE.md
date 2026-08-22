# External AGI Evidence Provenance

This layer strengthens the existing conservative AGI evidence gate. It does not replace it and does not turn development or reference results into claim-grade evidence.

## Purpose

The existing `EvidenceRecord` claim gate requires production-tier results, repeated coverage, independent evidence, a distinct producer/evaluator/attestor chain, and a trusted HMAC attestation supplied from outside the evidence itself. This module adds an earlier public-key provenance stage so an independent evaluator can sign its own raw result without sharing a private key with this repository.

The external signature is bound to the criterion, pass/fail result, source kind, task, domain, run, system producer, verifier identity, hidden-suite identifier/version/hash, artifact hash, result hash, a fresh challenge nonce, evaluation time, repeat index, and metadata. For claim-grade bridging, that signed metadata must include `evaluation_request_sha256`, binding the result to the exact strict handoff request described below. A complete directory handoff additionally requires signed `external_verifier_sha256` and `external_challenge_sha256` metadata so no verifier policy or challenge validity field can be altered while retaining an otherwise valid signature.

## Secret-free evaluator handoff

Before contacting an external evaluator, create the tracked-source archive, public runtime manifest, and strict request together so all three use the same exact system commit and digest:

```sh
agi-external-artifact create \
  --root . \
  --repository owner/repository \
  --commit-sha <40-hex-commit> \
  --producer-id <system-producer-id> \
  --provider <public-provider-id> \
  --model <public-model-id> \
  --runner <public-runner-id> \
  --archive-output external-handoff/system.tar \
  --metadata-output external-handoff/artifact.json \
  --system-manifest-output external-handoff/system-manifest.json \
  --request-output external-handoff/request.json
```

The request freezes the repository's strict six-criterion core policy and the default requirement for two cryptographically distinct external evaluator groups per criterion. Validation fails closed if those requirements are weakened, if the exact subject binding is malformed or tampered with, or if a private signing key, bridge secret, hidden seed, or expected answer is embedded. The request deliberately does not create an evaluator, hidden suite, challenge, result, signature, or production evidence; those remain independently controlled.

The independent evaluator must place the request's exact `request_sha256` value in the signed attestation metadata as `evaluation_request_sha256`. The claim bridge revalidates the referenced request from the ledger and rejects a missing request, a tampered request digest, an artifact mismatch, or a producer mismatch. Because the request digest covers repository, source commit, artifact digest, producer, six-criterion policy, and strict quorum, the evaluator signature can no longer be reassigned to another source commit or handoff while remaining claim-grade.

### Validate a complete returned handoff

For a returned evaluation, place exactly these public files in one dedicated directory (no extra files, directories, or symlinks):

- repository-produced `artifact.json`, `system.tar`, `system-manifest.json`, and `request.json`;
- evaluator-controlled `verifier.json`, `challenge.json`, `result.bin`, unsigned `statement.json`, and detached `signature.hex`.

The evaluator-controlled `challenge.json` uses this exact public schema (the `subject` object must exactly equal `request.json.subject`):

```json
{
  "schema_version": 1,
  "challenge_kind": "strict-external-evaluation-challenge-v1",
  "request_sha256": "<request.json request_sha256>",
  "criterion": "<one strict gate criterion>",
  "subject": {
    "repository": "owner/repository",
    "commit_sha": "<40-hex-commit>",
    "artifact_sha256": "<64-hex-source-artifact-digest>",
    "system_manifest_sha256": "<64-hex-runtime-manifest-digest>",
    "producer_id": "<system-producer-id>"
  },
  "nonce": "<64-hex-fresh-nonce>",
  "suite_id": "<independently-controlled-hidden-suite-id>",
  "suite_sha256": "<64-hex-frozen-suite-digest>",
  "issued_at": "<offset-aware-ISO-8601-time>",
  "expires_at": "<later-offset-aware-ISO-8601-time>"
}
```

Then validate every cross-binding and write the verified public attestation in one command:

```sh
agi-external-bundle external-handoff/ \
  --expected-verifier-id <independently-vetted-verifier-id> \
  --expected-public-key-hex <independently-vetted-32-byte-ed25519-public-key-hex> \
  --expected-criterion <strict-gate-criterion> \
  --output verified-attestation.json
```

The trust anchors are explicit command arguments rather than values silently accepted from the bundle. The validator independently revalidates the source archive, runtime manifest, strict request, verifier, challenge, raw result digest, canonical signed statement, and detached Ed25519 signature. The canonical bundle challenge explicitly binds the request digest, strict criterion, repository, subject commit, source artifact, runtime manifest, and producer before adding its hidden-suite digest, nonce, and validity window. The validator requires canonical digests of the complete verifier and challenge objects in the signed statement metadata, then cross-binds that statement to the same request, producer, verifier, criterion, suite, nonce, and raw result; it also binds the verifier registration to the supplied identity and public key. Unknown or missing members, duplicate JSON fields, non-finite JSON, non-canonical types, recursively secret-bearing fields, symlinks, and every mismatch fail closed. Output is atomic and is not written or replaced on failure.

This command accepts no private key, bridge secret, registry, or ledger path and performs no ledger mutation. A successful result proves only that the public handoff is internally and cryptographically bound. It does not prove evaluator independence, held-out status, clean disclosure history, quorum, production success, or AGI; those remain subsequent audit and claim-gate requirements.

## Evidence flow

1. **Create and preserve the strict request.** Put the validated request object in the ledger's `evaluation_requests` array. The exact `request_sha256` is the subject-binding identifier used by signed results.
2. **Register an independent verifier.** Add only a vetted verifier identity and Ed25519 public key to `evidence/external_verifiers.json`. Private keys stay with the independent evaluator. Do not register the system-under-test as its own verifier.
3. **Freeze the hidden suite.** Compute a SHA-256 digest of the exact external evaluation suite before the run.
4. **Issue a fresh challenge before evaluation.** Run `agi-external-evidence challenge evidence/external_ledger.json --suite-id <id> --suite-sha256 <sha256>`. The nonce is random, short-lived, and bound to the suite hash.
5. **Run externally.** The independent evaluator runs the frozen suite against the exact request-bound system artifact. The system under test must not control the evaluator's signing key, result hash, or verifier registry.
6. **Prepare the exact bytes to sign.** The independent evaluator copies the request's exact `request_sha256` into signed metadata as `evaluation_request_sha256`, the canonical complete `verifier.json` digest as `external_verifier_sha256`, and the canonical complete `challenge.json` digest as `external_challenge_sha256`, writes the public result statement without a `signature_hex` field, and runs `agi-external-evidence payload result-statement.json --output payload.json`. The output is exactly `ExternalAttestation.payload()` in canonical JSON form. Unknown, missing, non-canonical, incorrectly typed, or recursively secret-bearing fields are rejected before output. The command never accepts a private key.
7. **Sign outside the repository, then verify and package.** The evaluator signs the exact bytes of `payload.json` with its independently controlled Ed25519 key. Use `agi-external-bundle` for the complete returned directory so the detached signature cannot be packaged separately from a mismatched request, source artifact, runtime manifest, challenge, verifier identity, or raw result. The lower-level `agi-external-evidence finalize` command remains available for signature-only packaging, but does not provide those complete handoff cross-bindings. Neither command registers the verifier, mutates the ledger, or establishes production evidence.
8. **Preserve disclosure history.** If a held-out task becomes disclosed, append a disclosure entry with its task ID, suite hash, and disclosure timestamp. Evidence completed after disclosure is rejected as contaminated.
9. **Audit before promotion.** Run `agi-external-evidence audit evidence/external_ledger.json --registry evidence/external_verifiers.json --require-clean`. The audit verifies public-key signatures, verifier validity, challenge freshness, suite binding, held-out disclosure timing, challenge rebinding, exact attestation replay, and duplicate repeat-index inflation.
10. **Bridge only accepted, request-bound evidence into the existing claim gate.** `evaluate_external_ledger_claim()` revalidates every referenced strict request, verifies its canonical digest, checks the signed result's artifact and producer against the request subject, and then `bridge_external_attestation()` verifies the public-key evidence again before creating a production `EvidenceRecord`. The bridge secret must remain outside the repository. Its HMAC payload includes `provenance_ref`; the external attestation ID inside that reference covers signed metadata and therefore transitively covers `evaluation_request_sha256`.

## Fail-closed rules

Missing verifier registration, revoked/suspended/out-of-window verifier keys, invalid signatures, changed result/artifact hashes, expired or mismatched challenges, challenge reuse for another verifier/run/suite, duplicate task repeat indices, held-out disclosure before evaluation, missing/tampered strict request manifests, unsigned request bindings, or request-subject artifact/producer mismatches all reject claim-grade evidence. An external verifier cannot also be the system producer, and the bridge attestor must be a third identity distinct from both producer and evaluator.

The ledger intentionally starts empty. Empty evidence is not a successful AGI evaluation; it means no external signed trials have been recorded yet.

## Claim boundary

A clean provenance audit proves only that the supplied evidence objects satisfy the provenance protocol. It does **not** prove that the evaluated system is AGI. A positive claim still requires the repository's six-criterion evidence policy to pass using sufficient independently produced production-tier trials across distinct tasks, runs, domains, and attestors, with the strict request-to-system binding and unresolved production failures preserved and blocking where configured.

Reference-agent passes, CI passes, local/model-development runs, self-reports, unsigned results, a bare `independent=true` field, or a request manifest without independently signed production results remain non-claim-grade.
