# Independent evidence provenance

The strict AGI gate must not trust a model's own statement, a repository-local
benchmark, or an unsigned result uploaded after the fact. The provenance layer
in `src/agi/provenance.py` therefore requires a reproducible signed envelope for
every claim-admissible trial.

## Threat model

The protocol is designed to detect or reject:

- edited result JSON after evaluation;
- a claimed verifier that is not in the registry;
- two keys from one organization being counted as two independent verifiers;
- replay of an old evaluation under a new run name;
- reuse of one challenge by another verifier or run;
- evaluation after a held-out task was publicly disclosed;
- substitution of the evaluated suite, artifact, or result;
- non-canonical or malformed Ed25519 signatures;
- unsupported fields whose meaning is not covered by the signature.

It does not prove that a verifier is honest or that a benchmark measures AGI.
Those remain governance and scientific-validity questions. It makes the
submitted evidence tamper-evident and auditable.

## Files

```text
evidence/
  verifiers.json   public-key and independent-organization registry
  ledger.json      issued challenges, disclosure dates, and attestations
```

The committed files begin empty. An empty clean ledger is valid infrastructure,
not positive AGI evidence.

## Verifier registration

Each verifier entry must contain:

```json
{
  "verifier_id": "lab-a-key-2026-01",
  "independent_group": "independent-lab-a",
  "public_key_hex": "32-byte-ed25519-public-key-as-hex",
  "allowed_source_kinds": ["external_test"],
  "status": "active",
  "valid_from": "2026-08-16T00:00:00+00:00",
  "valid_until": null,
  "metadata": {}
}
```

The AGI evaluator counts `independent_group`, not the number of keys. Rotating a
key or registering several staff members at one organization cannot manufacture
independent verification.

## Fresh challenge

Before an external run, the evaluator issues a 32-byte random challenge bound to
a suite:

```bash
python -m agi.provenance challenge evidence/ledger.json \
  --suite-id held-out-suite-a \
  --ttl-minutes 60
```

A challenge may cover multiple task attestations from the same verifier, run,
and suite. Reusing it for another verifier, run, or suite is rejected. The
evaluation timestamp must be inside the challenge window.

## Signed attestation

The verifier signs canonical UTF-8 JSON containing all of these fields:

```json
{
  "schema_version": 1,
  "criterion": "robustness",
  "passed": true,
  "source_kind": "independent_reproduction",
  "task_id": "held-out-tool-failure-17",
  "domain": "software-operations",
  "run_id": "external-run-2026-08-16-a",
  "verifier_id": "lab-a-key-2026-01",
  "suite_id": "held-out-suite-a",
  "suite_version": "1.0",
  "suite_sha256": "64-lowercase-hex-characters",
  "artifact_sha256": "64-lowercase-hex-characters",
  "result_sha256": "64-lowercase-hex-characters",
  "challenge_nonce": "64-lowercase-hex-characters",
  "evaluated_at": "2026-08-16T08:30:00+00:00",
  "repeat_index": 0,
  "metadata": {},
  "signature_algorithm": "ed25519"
}
```

The detached 64-byte Ed25519 signature is added as `signature_hex`. An optional
`attestation_id` must equal the SHA-256 digest of the signed envelope. Unknown
fields are rejected rather than silently ignored.

Private keys must remain outside the repository. The implementation contains
strict verification only, plus a test-only signer used to prove interoperability
against an RFC 8032 test vector.

## Contamination registry

When a held-out task is disclosed, append a record before publishing it:

```json
{
  "task_id": "held-out-tool-failure-17",
  "suite_sha256": "64-lowercase-hex-characters",
  "disclosed_at": "2026-08-20T00:00:00+00:00"
}
```

An attestation completed at or after that time is excluded from claim evidence.
A new hidden task or suite version is then required.

## Audit

```bash
python -m agi.provenance audit evidence/ledger.json \
  --registry evidence/verifiers.json \
  --output agi-evidence-audit.json \
  --require-clean-ledger
```

`--require-clean-ledger` fails when any challenge, disclosure, or attestation is
malformed or rejected. `--require-agi-claim` additionally fails unless all six
strict capability criteria have sufficient admissible evidence. The scheduled
GitHub workflow audits integrity without pretending that an empty or incomplete
ledger proves AGI.
