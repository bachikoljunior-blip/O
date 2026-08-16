# Commit-reveal held-out evaluation

Public fixed tests are useful for regression, but they are weak evidence of
novel-task performance. A model may have seen them during training, an agent may
read the expected answers from the repository, or an adapter may accidentally
send the answer alongside the task. `src/agi/heldout.py` implements a two-phase
protocol that removes those shortcuts from the agent request.

## Protocol

1. The evaluator generates a random 32-byte suite seed.
2. Before any task runs, it publishes a commitment:
   `SHA-256(protocol || seed || canonical_config)`.
3. Tasks and private expected answers are generated deterministically from the
   still-secret seed.
4. The agent receives only each public instruction, input, response schema, task
   ID, domain, and criterion. It never receives the seed, private expected
   answer, or scoring object.
5. Every request and output is locked into a transcript digest.
6. Only after all outputs are fixed does the evaluator reveal the seed.
7. Anyone can regenerate the exact suite, verify the original commitment,
   recompute every task, output digest, score, suite digest, transcript digest,
   and report digest.
8. An independent verifier signs the resulting artifact through the provenance
   protocol before it can become claim-admissible evidence.

The commitment prevents the evaluator from choosing easier tasks after seeing
answers. The reveal prevents a hidden bespoke scorer from deciding results. The
external signature binds the report to a real independent evaluation.

## Prepare

First issue a fresh challenge in the evidence ledger, then use its nonce:

```bash
python -m agi.provenance challenge evidence/ledger.json \
  --suite-id held-out-suite-a \
  --ttl-minutes 60

python -m agi.heldout prepare \
  --suite-id held-out-suite-a \
  --suite-version 1.0 \
  --challenge-nonce <nonce-from-ledger> \
  --difficulty 2 \
  --secret-output heldout-secret.json \
  --commitment-output heldout-commitment.json
```

`heldout-secret.json` is created with owner-only permissions. Publish the
commitment document before starting the agent. Do not place the secret file in
the repository, an artifact store visible to the agent, or the agent's working
directory.

## Run

```bash
python -m agi.heldout run \
  --secret heldout-secret.json \
  --commitment heldout-commitment.json \
  --agent-command "/absolute/path/to/agent-adapter" \
  --timeout-seconds 60 \
  --output heldout-report.json
```

For each task, the adapter receives one JSON object on standard input:

```json
{
  "task": {
    "schema_version": 1,
    "sequence_index": 0,
    "task_id": "heldout-breadth-...",
    "criterion": "breadth",
    "domain": "arithmetic",
    "instruction": "...",
    "input_data": {},
    "response_schema": {}
  },
  "state": {}
}
```

It returns:

```json
{
  "output": {},
  "state": {}
}
```

The optional state is passed to the next task, enabling acquisition and delayed
recall tests. The process starts without a shell in a temporary working
directory and receives only a minimal environment. A serious external run must
add operating-system or virtual-machine isolation so the tested agent cannot
scan the evaluator's filesystem or inspect the secret seed through another
channel.

Omitting `--agent-command` runs the transparent development reference solver.
That validates the protocol machinery only. Its records are explicitly
`internal_test` and cannot support an AGI claim.

## Verify

```bash
python -m agi.heldout verify heldout-report.json
```

Verification regenerates all 12 randomized tasks, checks that no public request
changed, recomputes output hashes and scoring, validates the pre-run commitment,
and checks the locked transcript, suite, and report digests.

## Current scope

Protocol version 1 generates two randomized tasks for each of breadth, transfer,
autonomy, continual learning, self-improvement, and robustness. These are
stronger than a fixed smoke test but still only a development suite. A credible
AGI program must expand the hidden task families, use independent evaluators,
run multiple domains and repeats, retain negative results, test long-horizon
real-world work, and perform adversarial sandbox escape and contamination
reviews. The evidence gate must become harder as capability grows, not easier.
