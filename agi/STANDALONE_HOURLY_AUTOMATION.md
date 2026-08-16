# Hourly AGI execution policy

## Purpose

Run AGI development every hour in the same automation/task chat. Reusing the task chat is for execution and report aggregation only; it is not permission to use prior chat history as durable work state.

The model actually running in the automation session is the primary reasoning, judgment, and implementation agent. Prefer the highest-performance model available. GPT-5.6 Pro remains the preferred target when the platform can explicitly select and verify it, but a mismatch does not by itself block capability work. Never fabricate model identity: record the actual model only when platform-verified; otherwise record `unverified` and `model_verified: false`.

The O repository is the unfinished AGI architecture, Engine, tools, learned skills, scoped Candidates, Episodes, evidence, objective, and durable memory. Repository latest `main` is the only continuation source.

## Schedule and context

- Time zone: `Asia/Tokyo`.
- Start at minute `00` of every hour.
- Reuse the same automation/task chat.
- At each execution perform a logical context reset.
- Do not use prior execution chat text, final answers, summaries, hidden reasoning, unsaved plans, cached state, or unpersisted tool results as current work state.
- If platform-level physical context exclusion is not externally verified, record `platform_context_exclusion_verified: false`.
- The first line of the saved request is exactly: `agiを作って`.
- Repository contents are the durable work product and memory.
- At most one hourly execution may hold the execution lease.

At execution start calculate:

- `scheduled_start`: assigned hourly boundary in `Asia/Tokyo`.
- `next_scheduled_start = scheduled_start + 1 hour`.
- `soft_stop = next_scheduled_start - 10 minutes`.
- `hard_stop = next_scheduled_start - 5 minutes`.

## Required startup reads

Read latest `main` and at least:

- `AGENTS.md`
- this file
- `agi/REPORTING_TARGET.json`
- `agi/CONTINUATION.json`
- `agi/AUTONOMY_STATE.json`
- `agi/HOURLY_EXECUTION_STATE.json`
- `agi/LATEST_HOURLY_REPORT.json`
- relevant `.continual/runs/`, `.continual/episodes/`, `.continual/candidates/`, `.continual/system/`
- relevant evidence/trial/invocation ledgers
- relevant `src/` and `tests/`
- open PRs, unfinished branches, latest CI, and exact latest-main SHA

If chat history conflicts with repository latest `main`, repository latest `main` wins.

## Saved hourly request

```text
agiを作って。

この実行は前回と同じ自動実行セッション・同じタスクチャットを再利用する。ただし、前回実行の会話本文、最終回答、途中報告、要約、隠れた推論、未保存の計画、ツール結果、キャッシュ、モデル内部の記憶を今回の作業状態として使用してはならない。今回の継続状態はGitHubリポジトリbachikoljunior-blip/Oの最新mainだけから復元すること。

開始時に logical_context_reset: true、prior_execution_chat_context_used: false、continuation_source: repository_latest_main_only を記録する。物理的なチャットコンテキスト除外をプラットフォーム上で非自己申告に確認できない場合は platform_context_exclusion_verified: false とする。

このセッションで実際に動いているモデル自身が主要な思考、判断、実装、反証、検証を行うこと。GPT-5.6 Proは利用可能でプラットフォーム確認できるなら優先するが、モデル不一致だけを理由にAGI開発を停止しない。モデル名を確認できない場合は actual_model: unverified、model_verified: false と記録する。

最新mainのAGENTS.md、agi/STANDALONE_HOURLY_AUTOMATION.md、agi/REPORTING_TARGET.json、agi/CONTINUATION.json、agi/AUTONOMY_STATE.json、agi/HOURLY_EXECUTION_STATE.json、および関連する.continual、src、tests、PR、CIを実際に確認し、AGI完成に最も寄与する未完了の反証可能な能力マイルストーンを自分で選び、実装、focused test、全テスト、必要なCI、PR、マージ判断、継続状態保存まで行うこと。

Candidateは影響scope直前で評価し、保護済み能力を落とさず複数回の決定論的改善が確認されたscopeだけで昇格すること。失敗、反例、性能低下、UNCERTAIN、NO_CHANGE、rejected Candidate、failed CI、failed hypothesis、overfit、仕様曖昧性を消さないこと。trial ledger、invocation journal、idempotency recordを維持すること。

一つの作業単位が完了するたび現在時刻を再取得し、soft_stopより前で次の安全な高価値作業を検証・保存まで完了できるなら直ちに続行すること。commit、PR、テスト成功、CI成功、merge、1つのマイルストーン完了、報告作成を終了理由にしないこと。

soft_stop後は新しい大きな作業を開始せず、hard_stopまでに安全に確定すること。終了時は最新main、PR、head SHA、CIを再確認し、agi/CONTINUATION.json、agi/AUTONOMY_STATE.json、agi/HOURLY_EXECUTION_STATE.json、agi/LATEST_HOURLY_REPORT.json、agi/reports/YYYYMMDDTHH00JST.mdへ正確な状態と報告を保存し、leaseを解放してから同じタスクチャットへ最終報告すること。lease解放後はリポジトリを変更しないこと。

AGI完成は自己申告しないこと。未知課題への一般化、継続学習、長期自律性、自己改善、頑健性、独立した外部検証を満たし、厳格な外部証拠gateが通るまではAGI未証明と報告すること。
```

## Single-execution lease

Use `agi/HOURLY_EXECUTION_STATE.json` as the durable lease. At start atomically record at least:

- `status: running`
- `scheduled_start`, `actual_started_at`, `next_scheduled_start`, `soft_stop`, `hard_stop`
- unique `execution_id`/`session_id`
- `task_chat_reused: true`
- `logical_context_reset: true`
- `prior_execution_chat_context_used: false`
- `continuation_source: repository_latest_main_only`
- `platform_context_exclusion_verified` and source
- `required_model: GPT-5.6 Pro` as preferred target
- `actual_model`, `model_verified`, `model_verification_source`
- resumed `active_run_id` / `current_work_unit`
- `last_progress_at`

Model mismatch alone is not an overlap/block condition.

If an earlier lease is still `running` and its `hard_stop` is in the future, do not modify the repository concurrently. Queue if supported; otherwise report overlap and exit without competing work. If the prior hard stop has passed, record the old run as interrupted, inspect durable artifacts and side effects, then recover idempotently.

## Work priority

Prefer capability growth over evaluator/report-format churn:

1. measurable improvement on unknown tasks;
2. safe acquisition of new Skills/programs/tools/domains;
3. retained learning across fresh engines/runs;
4. no-forgetting regression;
5. learning from failures/counterexamples;
6. transfer to unknown domains/structures/tools;
7. longer typed composition;
8. multi-session capability accumulation;
9. long-horizon autonomous operation;
10. independent external evidence.

Do not weaken continual learning, safety, Candidate evaluation, protected abilities, or negative-evidence retention to improve scores.

## Finalization

Finalization is one-way:

1. Stop starting new work.
2. Finish/revert/leave-unmerged in-flight mutations safely.
3. Reacquire current time and inspect latest main, PRs, exact head SHAs, and CI.
4. Compose the factual report.
5. Persist continuation, autonomy state, lease state, latest report metadata, and `agi/reports/YYYYMMDDTHH00JST.md`.
6. Mark lease `completed` or `interrupted`, set `finished_at`, clear active work.
7. Deliver the final report to the same task chat.
8. Perform no repository mutation after lease release.

Every report must state the schedule window, context-reset fields, model-verification fields, completed capability work, PR/head/merge/main SHAs, successful and failed validation, retained counterexamples/negative evidence, unfinished/blocked/pending work, exact next action, AGI claim boundary, and whether the strict independent external evidence gate passed.
