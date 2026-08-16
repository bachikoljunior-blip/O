# Hourly standalone AGI execution policy

## Schedule

- Time zone: `Asia/Tokyo`.
- Start a new standalone session at minute `00` of every hour.
- Every scheduled execution starts with zero prior chat/session context.
- The first line of the saved request is exactly: `agiを作って`.
- Repository contents may be inspected as the durable work product, but no conclusions or hidden state from a previous chat may be assumed.

At session start, calculate and retain:

- `scheduled_start`: the current hourly boundary in `Asia/Tokyo`.
- `next_scheduled_start`: `scheduled_start + 1 hour`.
- `soft_stop`: `next_scheduled_start - 10 minutes`.
- `hard_stop`: `next_scheduled_start - 5 minutes`.

The fixed hourly boundaries are `00:00`, `01:00`, ..., `23:00` Japan time. For example, a session beginning during the 18:00 execution window must regard 19:00 as the next scheduled start, begin finalization no later than 18:50, and finish no later than 18:55.

## Saved standalone request

```text
agiを作って。

この実行は、過去のチャットや前回セッションのコンテキストを一切引き継がない新規スタンドアロンセッションである。最新の O リポジトリの main を実際に確認し、現在存在する成果物だけを根拠として、AGI完成に最も寄与する未完了作業を実行すること。以前のエージェントの説明や自己申告は事実として扱わない。

タイムゾーンは Asia/Tokyo。毎時00分が基準開始時刻である。開始時に現在時刻、今回の基準開始時刻、次回の基準開始時刻、次回10分前の soft stop、次回5分前の hard stop を確定すること。

一つの実装・検証・統合作業が完了するたび、必ず現在時刻を改めて取得すること。soft stop より前で、次の作業を検証・保存まで完了できる合理的な時間が残っているなら、完了報告で終了せず、直ちに次の最重要な未完了作業を選んで実行すること。この判定を、作業が完了するたびに毎回繰り返すこと。待機するだけの時間を作らず、ある作業が外部要因で実行不能なら、別の実行可能な高価値作業へ切り替えること。

soft stop 到達後は新しい大きな作業を開始せず、実行中の変更を検証し、失敗していれば安全に戻し、通過した変更だけを保存・統合し、正確な継続地点と次の実行可能作業をリポジトリへ残すこと。hard stop までに必ず終了すること。サブタスク完了、テスト成功、commit、PR、merge、報告は、時間が残っている限り終了理由にしないこと。

前回実行との重複を起こさないこと。原則として各実行を hard stop までに終了させる。万一、開始時に前回実行がまだ動いていることを確認した場合、新旧を並行実行せず、前回の終了を優先し、同じ変更を競合して実行しないこと。

AGI完成を自己申告で認定しないこと。未知課題への一般化、継続学習、長期自律性、自己改善、頑健性、および独立した外部検証の未達を正直に扱うこと。評価器や証拠形式だけを延々と改善せず、測定可能に能力が向上する知能本体の実装と反証可能な外部評価を優先すること。
```

## Overlap rule

The scheduler must allow at most one active execution for this automation. Prefer a hard finish before the next hourly boundary. If the scheduler supports concurrency queuing, configure one active run and queue the next run rather than cancelling the active run. A queued run must still start as a fresh standalone session with no inherited chat context.

## Completion rule

A session may finish before `soft_stop` only when no safe, executable, AGI-relevant work remains within its permissions. Before finishing, it must persist exact evidence, unfinished work, and the next executable action. It must not invent work merely to remain active, and it must not weaken the AGI evidence gate to produce a completion claim.
