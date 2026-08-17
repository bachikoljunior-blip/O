# External AGI Evidence Provenance

This layer strengthens the existing conservative AGI evidence gate. It does not replace it and does not turn development or reference results into claim-grade evidence.

## Purpose

The existing `EvidenceRecord` claim gate requires production-tier results, repeated coverage, independent evidence, a distinct producer/evaluator/attestor chain, and a trusted HMAC attestation supplied from outside the evidence itself. This module adds an earlier public-key provenance stage so an independent evaluator can sign its own raw result without sharing a private key with this repository.

The external signature is bound to the criterion, pass/fail result, source kind, task, domain, run, system producer, verifier identity, hidden-suite identifier/version/hash, artifact hash, result hash, a fresh challenge nonce, evaluation time, repeat index, and metadata.

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

## Evidence flow

1. **Register an independent verifier.** Add only a vetted verifier identity and Ed25519 public key to `evidence/external_verifiers.json`. Private keys stay with the independent evaluator. Do not register the system-under-test as its own verifier.
2. **Freeze the hidden suite.** Compute a SHA-256 digest of the exact external evaluation suite before the run.
3. **Issue a fresh challenge before evaluation.** Run `agi-external-evidence challenge evidence/external_ledger.json --suite-id <id> --suite-sha256 <sha256>`. The nonce is random, short-lived, and bound to the suite hash.
4. **Run externally.** The independent evaluator runs the frozen suite against the evaluated system. The system under test must not control the evaluator's signing key, result hash, or verifier registry.
5. **Sign the canonical result payload.** The independent evaluator signs the exact canonical JSON payload defined by `ExternalAttestation.payload()` with Ed25519 and returns the signature plus raw evidence artifacts.
6. **Preserve disclosure history.** If a held-out task becomes disclosed, append a disclosure entry with its task ID, suite hash, and disclosure timestamp. Evidence completed after disclosure is rejected as contaminated.
7. **Audit before promotion.** Run `agi-external-evidence audit evidence/external_ledger.json --registry evidence/external_verifiers.json --require-clean`. The audit verifies public-key signatures, verifier validity, challenge freshness, suite binding, held-out disclosure timing, challenge rebinding, exact attestation replay, and duplicate repeat-index inflation.
8. **Bridge only accepted evidence into the existing claim gate.** `bridge_external_attestation()` verifies the public-key evidence again, then creates a production `EvidenceRecord` and signs it with the repository's existing trusted-attestor HMAC mechanism. The bridge secret must remain outside the repository. Its HMAC payload conditionally includes `provenance_ref`, binding the external attestation ID, suite hash, result hash, challenge, and repeat index so the provenance chain cannot be substituted later.

## Fail-closed rules

Missing verifier registration, revoked/suspended/out-of-window verifier keys, invalid signatures, changed result/artifact hashes, expired or mismatched challenges, challenge reuse for another verifier/run/suite, duplicate task repeat indices, or held-out disclosure before evaluation all reject the evidence. An external verifier cannot also be the system producer, and the bridge attestor must be a third identity distinct from both producer and evaluator.

The ledger intentionally starts empty. Empty evidence is not a successful AGI evaluation; it means no external signed trials have been recorded yet.

## Claim boundary

A clean provenance audit proves only that the supplied evidence objects satisfy the provenance protocol. It does **not** prove that the evaluated system is AGI. A positive claim still requires the repository's six-criterion evidence policy to pass using sufficient independently produced production-tier trials across distinct tasks, runs, domains, and attestors, with unresolved production failures preserved and blocking where configured.

Reference-agent passes, CI passes, local/model-development runs, self-reports, unsigned results, or a bare `independent=true` field remain non-claim-grade.
