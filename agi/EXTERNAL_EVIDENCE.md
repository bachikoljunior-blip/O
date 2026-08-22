# External AGI Evidence Provenance

This layer strengthens the existing conservative AGI evidence gate. It does not replace it and does not turn development or reference results into claim-grade evidence.

## Purpose

The existing `EvidenceRecord` claim gate requires production-tier results, repeated coverage, independent evidence, a distinct producer/evaluator/attestor chain, and a trusted HMAC attestation supplied from outside the evidence itself. This module adds an earlier public-key provenance stage so an independent evaluator can sign its own raw result without sharing a private key with this repository.

The external signature is bound to the criterion, pass/fail result, source kind, task, domain, run, system producer, verifier identity, hidden-suite identifier/version/hash, artifact hash, result hash, a fresh challenge nonce, evaluation time, repeat index, and metadata. For claim-grade bridging, that signed metadata must include `evaluation_request_sha256`, binding the result to the exact strict handoff request described below.

## Secret-free evaluator handoff

Before contacting an external evaluator, create a machine-readable request bound to the exact system commit and artifact digest:

```sh
agi-external-request create \
  --repository owner/repository \
  --commit-sha <40-hex-commit> \
  --artifact-sha256 <64-hex-artifact-digest> \
  --producer-id <system-producer-id> \
  --output external-evaluation-request.json
agi-external-request validate external-evaluation-request.json
```

The request freezes the repository's strict six-criterion core policy and the default requirement for two cryptographically distinct external evaluator groups per criterion. Validation fails closed if those requirements are weakened, if the exact subject binding is malformed or tampered with, or if a private signing key, bridge secret, hidden seed, or expected answer is embedded. The request deliberately does not create an evaluator, hidden suite, challenge, result, signature, or production evidence; those remain independently controlled.

The independent evaluator must place the request's exact `request_sha256` value in the signed attestation metadata as `evaluation_request_sha256`. The claim bridge revalidates the referenced request from the ledger and rejects a missing request, a tampered request digest, an artifact mismatch, or a producer mismatch. Because the request digest covers repository, source commit, artifact digest, producer, six-criterion policy, and strict quorum, the evaluator signature can no longer be reassigned to another source commit or handoff while remaining claim-grade.

## Evidence flow

1. **Create and preserve the strict request.** Put the validated request object in the ledger's `evaluation_requests` array. The exact `request_sha256` is the subject-binding identifier used by signed results.
2. **Register an independent verifier.** Add only a vetted verifier identity and Ed25519 public key to `evidence/external_verifiers.json`. Private keys stay with the independent evaluator. Do not register the system-under-test as its own verifier.
3. **Freeze the hidden suite.** Compute a SHA-256 digest of the exact external evaluation suite before the run.
4. **Issue a fresh challenge before evaluation.** Run `agi-external-evidence challenge evidence/external_ledger.json --suite-id <id> --suite-sha256 <sha256>`. The nonce is random, short-lived, and bound to the suite hash.
5. **Run externally.** The independent evaluator runs the frozen suite against the exact request-bound system artifact. The system under test must not control the evaluator's signing key, result hash, or verifier registry.
6. **Prepare the exact bytes to sign.** The independent evaluator copies the request's exact `request_sha256` into signed metadata as `evaluation_request_sha256`, writes the public result statement without a `signature_hex` field, and runs `agi-external-evidence payload result-statement.json --output payload.json`. The output is exactly `ExternalAttestation.payload()` in canonical JSON form. Unknown, missing, non-canonical, incorrectly typed, or recursively secret-bearing fields are rejected before output. The command never accepts a private key.
7. **Sign outside the repository, then verify and package.** The evaluator signs the exact bytes of `payload.json` with its independently controlled Ed25519 key, then runs `agi-external-evidence finalize result-statement.json --public-key-hex <registered-public-key> --signature-hex <detached-signature> --output attestation.json`. Finalization writes no output unless the detached signature matches the canonical statement. It does not register the verifier, mutate the ledger, or establish production evidence.
8. **Preserve disclosure history.** If a held-out task becomes disclosed, append a disclosure entry with its task ID, suite hash, and disclosure timestamp. Evidence completed after disclosure is rejected as contaminated.
9. **Audit before promotion.** Run `agi-external-evidence audit evidence/external_ledger.json --registry evidence/external_verifiers.json --require-clean`. The audit verifies public-key signatures, verifier validity, challenge freshness, suite binding, held-out disclosure timing, challenge rebinding, exact attestation replay, and duplicate repeat-index inflation.
10. **Bridge only accepted, request-bound evidence into the existing claim gate.** `evaluate_external_ledger_claim()` revalidates every referenced strict request, verifies its canonical digest, checks the signed result's artifact and producer against the request subject, and then `bridge_external_attestation()` verifies the public-key evidence again before creating a production `EvidenceRecord`. The bridge secret must remain outside the repository. Its HMAC payload includes `provenance_ref`; the external attestation ID inside that reference covers signed metadata and therefore transitively covers `evaluation_request_sha256`.

## Fail-closed rules

Missing verifier registration, revoked/suspended/out-of-window verifier keys, invalid signatures, changed result/artifact hashes, expired or mismatched challenges, challenge reuse for another verifier/run/suite, duplicate task repeat indices, held-out disclosure before evaluation, missing/tampered strict request manifests, unsigned request bindings, or request-subject artifact/producer mismatches all reject claim-grade evidence. An external verifier cannot also be the system producer, and the bridge attestor must be a third identity distinct from both producer and evaluator.

The ledger intentionally starts empty. Empty evidence is not a successful AGI evaluation; it means no external signed trials have been recorded yet.

## Claim boundary

A clean provenance audit proves only that the supplied evidence objects satisfy the provenance protocol. It does **not** prove that the evaluated system is AGI. A positive claim still requires the repository's six-criterion evidence policy to pass using sufficient independently produced production-tier trials across distinct tasks, runs, domains, and attestors, with the strict request-to-system binding and unresolved production failures preserved and blocking where configured.

Reference-agent passes, CI passes, local/model-development runs, self-reports, unsigned results, a bare `independent=true` field, or a request manifest without independently signed production results remain non-claim-grade.
