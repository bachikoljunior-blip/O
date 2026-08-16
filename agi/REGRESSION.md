# Continual-learning regression gate

A system is not improving if it gains one skill by silently losing older ones.
`src/agi/regression.py` therefore compares every proposed Candidate against a
protected baseline before broader activation.

## Fail-closed policy

The strict default requires:

- every baseline measurement to be repeated for the Candidate;
- no new failure on a previously passing task;
- no protected score drop;
- a positive mean gain on the explicitly named target tasks;
- at least two repeats for every target task;
- unique `(task_id, repeat_index)` pairs.

A missing measurement is not neutral. It blocks adoption. A single unusually
good target run is not enough. Duplicate copies of one run are rejected before
scoring.

## Measurement shape

```json
{
  "task_id": "novel-transfer-17",
  "criterion": "transfer",
  "domain": "symbolic-reasoning",
  "repeat_index": 0,
  "passed": true,
  "score": 0.84,
  "artifact_sha256": "64-lowercase-hex-characters"
}
```

Scores are normalized to `[0, 1]`. The artifact digest binds the measurement to
its raw result. External use should derive these measurements only from the
signed provenance ledger or another independently auditable source.

## Command

```bash
python -m agi.regression \
  --baseline baseline.json \
  --candidate candidate.json \
  --target-task novel-transfer-17 \
  --candidate-id candidate-transfer-v3 \
  --output regression-decision.json
```

Exit code `0` means the Candidate passed the configured adoption gate. Exit code
`2` means it remains a Candidate. The report includes every missing measurement,
new failure, score drop, target repeat count, mean target gain, and a digest over
the complete comparison input.

## Scope of adoption

Passing this gate supports activation only for the scope represented by the
measurements. It does not justify global replacement. Wider activation requires
a wider protected matrix and additional independent evidence. Failed and
negative results must stay in the evidence history; they must not be overwritten
by later successful runs.
