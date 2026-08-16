# O — Evidence-driven continual agent and AGI research runtime

O is a persistent agent runtime that closes one task through:

`ENTRY → Root → Execute → Task Evaluate → Experience Fragments → Task Episode → Post-task Learn → Candidate`

It is designed to improve procedures from experience without silently replacing active behavior, replaying side effects after interruption, or calling a narrow success “AGI”.

## What changed in v0.2

- Active component pointers are now actually loaded from `.continual/system/active-components.json`.
- Candidate Preflight receives only Candidates relevant to the exact upcoming component, plus dependencies.
- Every model invocation has a deterministic journal. Completed output is reused after restart instead of invoking the model/tool loop again.
- Model outputs are contract-validated, with one explicit repair attempt.
- Step exhaustion produces a resumable checkpoint instead of converting time limits into task failure.
- Candidate proposals with an existing ID merge evidence and source references rather than disappearing.
- Candidate prompts default to scoped overlays, so a learned instruction cannot accidentally replace the base component contract.
- Model-run commands receive a secret-stripped environment; network/publish commands and direct edits to active control files are blocked.
- The `agi` package is installed correctly and supplies a conservative evidence ledger plus a cross-criterion development benchmark.

## Installation

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
export OPENAI_API_KEY='...'
export OPENAI_MODEL='gpt-5.6'
```

Never commit API keys.

## Persistent task execution

```bash
continual start "ユーザー依頼" --max-steps 64
continual status <run_id>
continual resume <run_id> --max-steps 64
continual resume-all --max-steps 16
continual feedback <episode_id> "結果へのユーザーフィードバック"
```

The source of truth is under `.continual/`, not the chat transcript.

## AGI evidence commands

```bash
agi-benchmark validate-suite
agi-benchmark run-reference --output .continual/evidence/reference-report.json
agi-benchmark run-openai --model gpt-5.6 --output .continual/evidence/openai-development-report.json
agi-benchmark evaluate evidence.json
```

`run-reference` validates the harness with a task-specific reference adapter. It is deliberately **not** AGI evidence. `run-openai` produces development evidence by default. The claim evaluator requires repeated, independent, production-tier evidence across breadth, transfer, autonomy, continual learning without regression, tested self-improvement, and robustness.

A development benchmark may pass while `agi_claim_supported` remains false. That separation is intentional: progress must not be converted into a false completion claim.

## Repository map

- `src/continual/`: persistent orchestration, contracts, safe model tools, state store.
- `prompts/`: active semantic components; changes should normally enter as Candidates.
- `.continual/runs/`: snapshots, invocation journals, fragments, artifacts, trials, events.
- `.continual/episodes/`: consolidated task episodes and later relations/feedback.
- `.continual/candidates/`: Candidate metadata, scoped evidence, and optional Candidate prompts.
- `src/agi/`: AGI criteria, evidence policy, benchmark protocol, OpenAI adapter, and CLI.
- `agi/README.md`: current AGI research status and next falsifiable milestones.

See `SYSTEM_DESIGN.md` for restart and Candidate semantics.
