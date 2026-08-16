# Hourly standalone AGI execution policy

## Purpose

Use the ChatGPT automation that creates a new standalone session for every scheduled execution. The new session is not the AGI worker. It is the runtime host, authenticated tool boundary, and safety/validation supervisor for the unfinished AGI persisted in this repository.

The unfinished AGI consists of the repository's durable objective and execution state, including `agi/CONTINUATION.json`, `agi/AUTONOMY_STATE.json` when present, `.continual/`, `src/agi/autonomy.py`, `src/continual/engine.py`, active skills, scoped Candidates, Episodes, and evidence. Each fresh session must load and execute that system rather than substitute a separate one-off development plan based on chat context.

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

## Worker identity

1. Read `AGENTS.md`, this file, `agi/CONTINUATION.json`, and `agi/HOURLY_EXECUTION_STATE.json` first.
2. Acquire the single-execution lease described below.
3. Load the unfinished AGI's persisted state. Resume a non-terminal run when one exists; otherwise start its next run from the current persisted objective.
4. The host session may provide reasoning and tools only as the execution substrate requested by the unfinished AGI. It must not silently replace the unfinished AGI with an unrelated plan.
5. Prefer the repository's actual Engine/autonomy entry points. Where the environment cannot invoke them directly, execute the same persisted state machine and contracts explicitly, and persist equivalent run, Episode, Candidate, evidence, and continuation records.
6. A completed subtask, test, commit, PR, merge, or report is not completion of the user-level AGI objective.

## Saved standalone request

```text
agiを作って。

この実行は、過去のチャットや前回セッションのコンテキストを一切引き継がない新規スタンドアロンセッションである。新規セッション自身は起動・ツール提供・安全監督・外部検証のホストであり、作業主体は O リポジトリに永続化された未完成AGIである。

最初に最新mainの AGENTS.md、agi/STANDALONE_HOURLY_AUTOMATION.md、agi/CONTINUATION.json、agi/HOURLY_EXECUTION_STATE.json、利用可能なら agi/AUTONOMY_STATE.json と .continual の実行状態を実際に読むこと。前回チャットの説明や自己申告は使わず、リポジトリ内の証拠と状態だけから未完成AGIを再開すること。未完成AGIの Engine、Skill、Candidate、Episode、Memory、評価・学習手順を実行し、新規セッション独自の別計画へ置き換えないこと。

タイムゾーンは Asia/Tokyo。毎時00分が基準開始時刻である。開始時に現在時刻、今回の scheduled_start、次回の next_scheduled_start、次回10分前の soft_stop、次回5分前の hard_stop を確定し、agi/HOURLY_EXECUTION_STATE.json の単一実行leaseを取得すること。前回の有効なleaseが残っている場合は並行実行しない。スケジューラが待機を扱えるなら前回終了まで待機し、扱えないなら競合する変更をせず終了すること。期限切れleaseは中断として記録し、永続状態から安全に復旧すること。

一つの実装・検証・統合・学習単位が完了するたび、必ず現在時刻を改めて取得し、leaseのlast_progress_atと継続地点を更新すること。soft_stopより前で、次の作業を検証・保存まで完了できる合理的な時間が残っているなら、完了報告で終了せず、未完成AGIに直ちに次の最重要な実行可能作業を選択・実行させること。この判定を作業単位の完了ごとに毎回繰り返すこと。外部要因で一つが実行不能なら、待機せず別の実行可能な高価値作業へ切り替えること。

soft_stop到達後は新しい大きな作業を開始しない。実行中の変更を検証し、失敗していれば安全に戻し、通過した変更だけを保存・統合すること。正確な証拠、未完了作業、次の実行可能作業をリポジトリに残し、leaseを完了状態にしてhard_stopまでに必ず終了すること。次回毎時00分の新規セッションがゼロコンテキストから即再開できる状態を作ること。

AGI完成を自己申告で認定しないこと。未知課題への一般化、継続学習、長期自律性、自己改善、頑健性、および独立した外部検証の未達を正直に扱うこと。評価器や証拠形式だけを延々と改善せず、測定可能に能力が向上する知能本体の実装、未知課題への転移、反証可能な外部評価を優先すること。
```

## Single-execution lease

The durable lease is `agi/HOURLY_EXECUTION_STATE.json`.

At start, the session must atomically record:

- `status: running`
- `scheduled_start`, `next_scheduled_start`, `soft_stop`, and `hard_stop`
- a unique `session_id`
- `started_at` and `last_progress_at`
- the resumed `run_id` or exact current work unit

Overlap handling:

- If the file says `running` and its `hard_stop` is still in the future, a second session must not modify the repository.
- Prefer scheduler-side queuing until the lease holder finishes.
- If queuing is unavailable, the later session exits without competing work; the next hourly run remains fresh.
- If the recorded `hard_stop` has passed, mark the prior session `interrupted`, preserve its identifiers, inspect durable artifacts, and recover idempotently before acquiring a new lease.

At every completed work unit, update `last_progress_at`, evidence, and the exact next action. At finalization, mark `status: completed` or `status: interrupted`, record `finished_at`, and clear the active work unit.

## Completion rule

A session may finish before `soft_stop` only when the unfinished AGI has no safe, executable, AGI-relevant work within available permissions. Before finishing, it must persist exact evidence, failures, unknowns, unfinished work, and the next executable action. It must not invent work merely to remain active and must not weaken the AGI evidence gate to produce a completion claim.
