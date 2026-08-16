# Hourly standalone AGI execution policy

## Purpose

Use the ChatGPT automation that creates a new standalone session for every scheduled execution. The automation must select the highest-performance available ChatGPT model; the current required target is **GPT-5.6 Pro**.

The fresh session's model is the primary reasoning and implementation agent. The O repository supplies the unfinished AGI architecture, executable Engine, tools, learned skills, scoped Candidates, Episodes, evidence, objective, and durable memory. The repository state must guide and persist the work, but it is not a separate model that replaces the fresh session's reasoning.

Do not delegate central reasoning or implementation to Copilot or a lower-capability fallback unless the user explicitly changes this requirement. Record the actual model used in `agi/HOURLY_EXECUTION_STATE.json`.

The primary report destination is the existing management/control chat described by `agi/REPORTING_TARGET.json`. Reporting to that chat is an output-only action: the fresh hourly session must not read, import, or rely on that chat's prior context. A report counts as delivered to the control chat only when the execution platform exposes and confirms that exact destination. Never invent a conversation identifier.

## Schedule

- Time zone: `Asia/Tokyo`.
- Start a new standalone session at minute `00` of every hour.
- Every scheduled execution starts with zero prior chat/session context.
- The first line of the saved request is exactly: `agiを作って`.
- Repository contents are the durable work product and memory. No conclusion, hidden state, or completion claim from a previous chat may be assumed.
- At most one hourly session may hold the execution lease.

At session start, calculate and retain:

- `scheduled_start`: the hourly boundary assigned to this execution in `Asia/Tokyo`, even if launch is delayed.
- `next_scheduled_start`: `scheduled_start + 1 hour`.
- `soft_stop`: `next_scheduled_start - 10 minutes`.
- `hard_stop`: `next_scheduled_start - 5 minutes`.

The fixed boundaries are `00:00`, `01:00`, ..., `23:00` Japan time. A session assigned to the 19:00 window must start finalization no later than 19:50 and finish no later than 19:55, leaving the 20:00 session able to start from zero context.

## Execution identity

1. Read `AGENTS.md`, this file, `agi/REPORTING_TARGET.json`, `agi/CONTINUATION.json`, `agi/AUTONOMY_STATE.json`, and `agi/HOURLY_EXECUTION_STATE.json` first.
2. Acquire the single-execution lease described below and record the actual model. The current target is GPT-5.6 Pro.
3. Load the persisted unfinished objective and all relevant Engine, Skill, Candidate, Episode, Memory, and evidence state.
4. The fresh session's highest-performance model must itself determine and implement the highest-value falsifiable next capability or evidence milestone. It may execute or improve the repository's Engine, but must not blindly follow stale self-claims or treat a previous model's plan as evidence.
5. Resume a valid non-terminal run where appropriate; otherwise create the next run from the persisted user-level AGI objective.
6. A completed subtask, test, commit, PR, merge, or report is not completion of the user-level AGI objective.

## Saved standalone request

```text
agiを作って。

この実行は、過去のチャットや前回セッションのコンテキストを一切引き継がない新規スタンドアロンセッションである。この新規セッションで利用可能な最高性能モデルを使うこと。現在の指定はGPT-5.6 Proであり、あなた自身が主要な思考・判断・実装を行うこと。中心的な思考や実装をCopilotや低性能モデルへ委譲しないこと。

最初に最新mainのAGENTS.md、agi/STANDALONE_HOURLY_AUTOMATION.md、agi/REPORTING_TARGET.json、agi/CONTINUATION.json、agi/AUTONOMY_STATE.json、agi/HOURLY_EXECUTION_STATE.json、および関連する.continualと実装コードを実際に読むこと。前回チャットの説明や自己申告は使わず、リポジトリ内の証拠と状態だけから判断すること。Oリポジトリは未完成AGIのアーキテクチャ、Engine、Skill、Candidate、Episode、Memory、証拠、目的、永続状態であり、あなたはそれを読み、実行し、必要に応じて改善する最高性能の推論主体である。

タイムゾーンはAsia/Tokyo。毎時00分が基準開始時刻である。開始時に現在時刻、今回のscheduled_start、次回のnext_scheduled_start、次回10分前のsoft_stop、次回5分前のhard_stopを確定し、agi/HOURLY_EXECUTION_STATE.jsonの単一実行leaseを取得すること。実際に使用しているモデル名も記録すること。前回の有効なleaseが残っている場合は並行実行しない。期限切れleaseは中断として記録し、永続状態から安全かつ冪等に復旧すること。

評価器や証拠形式だけを増やすのではなく、未知課題に対して測定可能に能力が向上する知能本体を優先すること。最新mainを検査し、AGI完成に最も寄与する未完了作業を自分で選び、実装、テスト、反証、保存まで行うこと。AGI完成を自己申告で認定せず、未知課題への一般化、継続学習、長期自律性、自己改善、頑健性、独立した外部検証の未達を正直に扱うこと。

一つの実装・検証・統合・学習単位が完了するたび、必ず現在時刻を改めて取得し、leaseのlast_progress_atと継続地点を更新すること。soft_stopより前で、次の作業を検証・保存まで完了できる合理的な時間が残っているなら、完了報告で終了せず、直ちに次の最重要な実行可能作業へ進むこと。この判定を作業単位の完了ごとに毎回繰り返すこと。外部要因で一つが実行不能なら、待機せず別の実行可能な高価値作業へ切り替えること。

soft_stop到達後は新しい大きな作業を開始しない。実行中の変更を検証し、失敗していれば安全に戻し、通過した変更だけを保存・統合すること。最終化では、以後の能力変更を止め、最新mainと最終CI・PR状態を改めて確認し、正確な証拠、失敗、未知、未完了作業、次の実行可能作業を保存すること。今回の完全なユーザー向け報告をagi/reports配下へ保存し、agi/LATEST_HOURLY_REPORT.jsonを更新してから、leaseをcompletedまたはinterruptedにしてhard_stopまでに解放すること。lease解放後にcommit、merge、PR更新その他のリポジトリ変更を行ってはならない。遅れて完了したCIやPRは次回の新規セッションが整合させる未完了事項として残すこと。

最後に、agi/REPORTING_TARGET.jsonで指定された既存の管理チャットへ、保存済み報告と同一内容を投稿すること。これは出力配信だけであり、その管理チャットの過去内容を読んだりコンテキストへ取り込んだりしてはならない。プラットフォームが親・管理チャットへの投稿経路を実際に公開し、配信先を確認できる場合だけ直接投稿すること。経路が無い、識別子が無い、または配信確認が取れない場合は、この管理チャットへ配信したと主張してはならない。その場合は、新規スタンドアロンチャットの最終メッセージで、直接配信が未確認であることと、agi/LATEST_HOURLY_REPORT.jsonおよび具体的なagi/reports内の報告パスを明記すること。

報告には、今回の基準開始・実開始・終了・次回開始時刻、実際のモデル、完了した変更、PR番号とmerge/head SHA、成功した検証、保持した失敗、未マージまたはblockedの作業、正確な次の実行作業、AGI主張の境界を含めること。変更が無かった回、重複回避、モデル不一致、権限不足、失敗、中断の回でも報告を省略しないこと。隠れたchain-of-thoughtは出さず、行動、判断、証拠、失敗、未知だけを要約すること。
```

## Single-execution lease

The durable lease is `agi/HOURLY_EXECUTION_STATE.json`.

At start, the session must atomically record:

- `status: running`
- `scheduled_start`, `next_scheduled_start`, `soft_stop`, and `hard_stop`
- a unique `session_id`
- the actual `model` and its role
- `started_at` and `last_progress_at`
- the resumed `run_id` or exact current work unit

Overlap handling:

- If the file says `running` and its `hard_stop` is still in the future, a second session must not modify the repository.
- Prefer scheduler-side queuing until the lease holder finishes.
- If queuing is unavailable, the later session exits without competing work; the next hourly run remains fresh.
- An overlap/no-work session must still produce the mandatory report. Prefer the configured control chat; otherwise persist the report and post the declared standalone fallback notice.
- If the recorded `hard_stop` has passed, mark the prior session `interrupted`, preserve its identifiers, inspect durable artifacts, and recover idempotently before acquiring a new lease.

At every completed work unit, update `last_progress_at`, evidence, and the exact next action. At finalization, mark `status: completed` or `status: interrupted`, record `finished_at`, and clear the active work unit.

## Finalization transaction

Finalization is ordered and one-way:

1. Stop starting new work and safely finish, revert, or leave unmerged every in-flight change.
2. Reacquire the current time and inspect the latest main, PR state, and exact-head validation state.
3. Compose the complete factual user report.
4. Persist completed work, retained failures, unfinished work, the next action, the report under `agi/reports/`, and the updated `agi/LATEST_HOURLY_REPORT.json`.
5. Mark the lease `completed` or `interrupted`, set `finished_at`, and clear active work in the same final repository mutation sequence.
6. Attempt to deliver the exact persisted report to the configured control chat using only a platform-confirmed route.
7. If direct delivery is unavailable or unconfirmed, post a fallback notice in the standalone execution chat naming the persisted report path and explicitly stating that control-chat delivery was not confirmed.
8. Perform no repository mutation after step 5 and no further work after reporting. Late CI or PR results belong to the next zero-context session.

This ordering prevents a result from being merged after the saved continuation and report have declared the run complete.

## Mandatory user-visible report

Every hourly run must produce a factual report containing:

- scheduled window, actual start and finish, next scheduled start, and actual model;
- completed work units and the user-relevant capability or integrity change;
- every created, merged, unmerged, or closed PR and relevant head/merge SHA;
- successful checks and retained failed checks or counterexamples;
- unfinished, blocked, or pending work and the exact next executable action;
- an explicit statement of what remains before verified AGI and whether the strict external evidence gate passed;
- `control_chat_delivery_confirmed: true|false` and the persisted report path.

The primary destination is the configured control chat. The standalone execution chat is only a fallback when direct control-chat routing is unavailable or unconfirmed. The report is required for successful, no-change, overlap, model-mismatch, failed, blocked, and interrupted runs. It must not include hidden chain-of-thought and must not treat an internal benchmark, CI run, reference solver, architecture, or self-report as verified AGI evidence.

## Completion rule

A session may finish before `soft_stop` only when no safe, executable, AGI-relevant work remains within available permissions. Before finishing, it must persist exact evidence, failures, unknowns, unfinished work, the next executable action, and the complete user report. It must not invent work merely to remain active and must not weaken the AGI evidence gate to produce a completion claim. The final report is mandatory but is not completion of the user-level AGI objective.
