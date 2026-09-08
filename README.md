# O — Evidence-driven continual agent and AGI research runtime

O is a persistent agent runtime that closes one task through:

`ENTRY → Root → Execute → Task Evaluate → Experience Fragments → Task Episode → Post-task Learn → Candidate`

It is designed to improve procedures from experience without silently replacing active behavior, replaying side effects after interruption, or calling a narrow success “AGI”.

## What changed in v0.3

Version 0.2 made the continual runtime restart-safe, contract-validated, scoped, and evidence-driven. Version 0.3 adds a second evaluation layer that requires an agent to operate a tool-mediated workspace over multiple turns.

- A deterministic in-memory workspace exposes `list_files`, `read_file`, `write_file`, `remember`, and `adopt_procedure`.
- Twelve workspace tasks cover breadth, transfer, autonomy, continual learning, self-improvement, and robustness, with at least two tasks per criterion.
- Autonomy tests require observed recovery from an injected transient tool failure rather than a lucky guessed answer.
- Continual-learning and self-improvement tasks carry memory and procedures into later tasks and verify their reuse.
- A prompt-injection task contains a hidden forbidden secret path; attempting to read it is a hard failure even when the final answer is otherwise correct.
- Repeated campaigns run both the one-turn suite and the workspace suite, persist per-run reports plus an evidence ledger, and keep all results at development tier by default.
- The OpenAI adapter uses the Responses API and omits task IDs and hidden expected outputs from model payloads.
- The default API model is the stable `gpt-5` alias; `OPENAI_MODEL` or `--model` can override it.

The fixed kernel still includes deterministic invocation journals, completed-output reuse after restart, strict model-output contracts with one repair attempt, Candidate overlays, secret-stripped subprocess environments, and protected control-file boundaries.

## Installation

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
export OPENAI_API_KEY='...'
export OPENAI_MODEL='gpt-5'
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

## AGI development and evidence commands

```bash
# Validate both suite definitions.
agi-benchmark validate-suite
agi-benchmark validate-workspace-suite

# Validate the harnesses with task-specific reference scripts.
agi-benchmark run-reference --output /tmp/core-reference.json
agi-benchmark run-workspace-reference --output /tmp/workspace-reference.json
agi-benchmark run-campaign-reference --runs 2 --output-dir /tmp/reference-campaign

# Run a real model at development evidence tier.
agi-benchmark run-openai --model gpt-5 --output /tmp/core-openai.json
agi-benchmark run-workspace-openai --model gpt-5 --output /tmp/workspace-openai.json
agi-benchmark run-campaign-openai \
  --model gpt-5 \
  --runs 2 \
  --campaign-id openai-development \
  --output-dir .continual/evidence/openai-development

# Evaluate an evidence ledger under the conservative claim policy.
agi-benchmark evaluate evidence.json
```

Reference scripts validate the harness only and are deliberately excluded from AGI evidence. Real-model campaigns are also development evidence unless an independent production process supplies claim-grade records. A campaign may pass every development task while `agi_claim_supported` remains false; that separation prevents architecture, self-report, or benchmark-specific code from becoming a false completion claim.

A manually dispatchable GitHub workflow is included for live campaigns. It requires an `OPENAI_API_KEY` repository secret and stores reports as an Actions artifact. The normal CI never makes paid model calls.

## Repository map

- `src/continual/`: persistent orchestration, contracts, safe model tools, and state store.
- `prompts/`: active semantic components; changes should normally enter as Candidates.
- `.continual/runs/`: snapshots, invocation journals, fragments, artifacts, trials, and events.
- `.continual/episodes/`: consolidated task episodes and later relations/feedback.
- `.continual/candidates/`: Candidate metadata, scoped evidence, and optional Candidate prompts.
- `src/agi/benchmark.py`: one-turn development suite.
- `src/agi/workspace.py`: multi-turn tool environment, policy checks, and workspace suite.
- `src/agi/campaign.py`: repeated campaign runner and evidence ledger generation.
- `src/agi/evaluation.py`: conservative AGI claim policy.
- `agi/README.md`: current research status and next falsifiable milestones.

See `SYSTEM_DESIGN.md` for restart, Candidate, workspace, and evidence semantics.
