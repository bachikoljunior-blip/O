# AGI watchdog execution policy

## Purpose

Continue development of the unfinished AGI system until the repository's strict independent external evidence gate supports a verified AGI claim. The recurring hourly trigger is not a one-hour work window. It is only a watchdog that recovers development if the active execution has stopped.

The model actually running in the automation/task chat is the primary reasoning, judgment, falsification, implementation, and validation agent. Prefer the highest-performance model the platform can explicitly select and verify. GPT-5.6 Pro remains preferred when available, but model mismatch alone must not stop useful capability work. Never fabricate model identity.

Repository latest `main` is the only durable continuation source. The repository contains the unfinished Engine, Skills, scoped Candidates, Episodes, Memory, evidence, safety rules, objective, and continuation state.

## Context boundary

At every watchdog recovery:

- perform a logical context reset;
- do not use prior execution chat text, prior final answers, summaries, hidden reasoning, cached plans, unpersisted tool results, or model memory as work state;
- read repository latest `main` and reconstruct the current objective, evidence, failures, open work, PR/CI state, and next action from it;
- if physical platform context exclusion cannot be independently verified, record `platform_context_exclusion_verified: false` rather than claiming isolation.

The saved request starts with exactly `最短でagiを作って。`.

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
- relevant evidence, trial, invocation, and idempotency ledgers
- relevant `src/` and `tests/`
- open PRs, unfinished branches, exact CI heads, and latest-main SHA

If chat history conflicts with latest `main`, latest `main` wins.

## Watchdog and single-execution lease

Use `agi/HOURLY_EXECUTION_STATE.json` as the durable single-execution lease.

At watchdog start, capture the actual trigger time and inspect the lease before any repository mutation. The hourly boundary may still be resolved and stored for audit using platform-assigned scheduled metadata when available or `src/agi/hourly_window.py` otherwise. Derived `next_scheduled_start`, `derived_soft_stop`, and `derived_hard_stop` may be retained for audit compatibility, but in watchdog mode they are **not execution stop conditions**.

The active watchdog state must record at least:

- `status: running` while work is alive;
- `lease_mode: watchdog_continuation`;
- trigger/audit schedule fields;
- `time_budget_enabled: false`;
- `soft_stop: null`;
- `hard_stop: null`;
- unique execution/session/run identifiers;
- `heartbeat_at` and `last_progress_at`;
- a finite `stale_after_seconds` recovery threshold;
- `logical_context_reset: true`;
- `prior_execution_chat_context_used: false`;
- `continuation_source: repository_latest_main_only`;
- model verification fields;
- current falsifiable work unit and exact latest-main SHA at recovery.

If a recent `running` heartbeat is still inside the stale threshold, another execution is alive. Do not create a competing branch, commit, PR, merge, or other repository mutation; the watchdog exits without duplicating work.

If the prior lease is stale, inspect its PRs, branches, CI, commits, and durable evidence, then recover idempotently from latest `main`. Record the stale execution, last progress, and recovery reason. Do not repeat an external side effect merely because the old process disappeared.

## Continuous execution rule

Once an execution owns the lease, it does **not** stop because an hourly boundary arrived or because a derived time budget expired. Continue while the AGI objective is unverified and useful safe executable work exists.

A commit, focused-test pass, full-test pass, CI pass, PR creation, PR merge, report, checkpoint, or completion of one milestone is never by itself a reason to stop. After each completed work unit, immediately select the next highest-value falsifiable AGI milestone and continue.

If CI or another external response is pending, perform non-conflicting useful work when possible. If one capability path is blocked, select another high-value path rather than treating the blocker as completion. Preserve exact continuation state frequently enough that a process loss can be recovered without rediscovery.

The platform may still terminate an individual process or tool session. That external termination cannot be prevented by repository policy. Before any unavoidable process end that is observable in advance, persist exact continuation, unfinished work, evidence, in-flight PR/CI state, and the next executable action. A later watchdog recovery then resumes from latest `main`.

## Work priority

Prefer capability growth and falsifiable retained improvement over formatting churn:

1. measurable improvement on previously unknown tasks;
2. safe acquisition of new Skills/programs/tools/domains;
3. retained learning across fresh Engines and later campaigns;
4. no-forgetting regression and rollback safety;
5. learning from failures and committed counterexamples;
6. transfer across unknown domains, structures, and tools;
7. longer typed composition;
8. multi-session capability accumulation;
9. long-horizon autonomous operation;
10. genuinely independent, cryptographically auditable production evidence.

Do not weaken continual learning, safety, Candidate evaluation, protected abilities, negative-evidence retention, evaluator independence, or the external claim gate to improve scores.

## Candidate and evidence rules

Evaluate a Candidate immediately before the exact scope it can affect. Promote only after repeated deterministic target improvement and protected-capability retention required by the repository gate. Preserve failed heads, failed CI, rejected Candidates, contradictory evidence, counterexamples, `UNCERTAIN`, `NO_CHANGE`, overfit evidence, and specification ambiguity.

Maintain trial ledgers, invocation journals, idempotency records, contamination checks, and rollback evidence. Exact-head CI must pass before merge. Never merge a failed, stale, moved, or otherwise unverified head merely to maintain momentum.

## AGI claim boundary

Do not call the system AGI because of architecture, model identity, self-report, internal benchmarks, bounded synthesis, internal evaluator results, Candidate promotion, successful CI, long execution, or repository-authored test doubles.

A verified AGI claim requires the strict external production evidence gate to pass on genuinely independent, auditable, cryptographically bound evidence across the required breadth, transfer, autonomy, continual learning, self-improvement, and robustness criteria, with the repository's required independent evaluator quorum and no unresolved admissible failures or contamination.

Until that gate passes, report the system as unfinished and continue development.

## Saved watchdog request

```text
最短でagiを作って。

この実行はGitHubリポジトリ bachikoljunior-blip/O の最新mainだけから作業状態を復元する。前回実行の会話本文、最終回答、要約、隠れた推論、未保存の計画、ツール結果、キャッシュ、モデル内部の記憶を今回の作業状態として使用しない。

最初にAGENTS.md、agi/STANDALONE_HOURLY_AUTOMATION.md、agi/REPORTING_TARGET.json、agi/CONTINUATION.json、agi/AUTONOMY_STATE.json、agi/HOURLY_EXECUTION_STATE.json、関連する.continual、src、tests、PR、CIを確認する。

毎時発火は監視役だけである。HOURLY_EXECUTION_STATE.json に stale_after_seconds 未満の新しい running heartbeat があるなら既存実行が生きているため重複変更せず終了する。lease が stale または解放済みでAGI未達なら、最新mainから安全に復旧して続行する。

復旧した実行では time_budget_enabled: false、soft_stop: null、hard_stop: null を保持する。毎時境界、derived_soft_stop、derived_hard_stop は監査情報にすぎず終了条件として使用しない。実行は検証済みAGI達成まで、またはプラットフォーム／プロセス自体が終了するまで継続し、heartbeatと正確な継続状態を永続化する。

報告、commit、テスト成功、CI成功、PR作成、merge、単一マイルストーン完了を終了理由にしない。各作業単位の直後に次の最重要で反証可能なAGIマイルストーンを選び、実装・検証・証拠保存まで続ける。

Candidateは影響scope直前で評価し、保護済み能力を落とさず必要な反復改善が確認されたscopeだけで昇格する。失敗、反例、性能低下、UNCERTAIN、NO_CHANGE、rejected Candidate、failed CI、failed hypothesis、overfitを消さない。trial ledger、invocation journal、idempotency recordを維持する。

変更は段階的に検証し、正確なheadの必要CI/checkが成功した変更だけをmainへ統合する。AGI完成は自己申告しない。厳格な独立外部証拠gateが全必要条件で通るまではAGI未証明であり、開発を続ける。

プロセス終了を避けられない場合は、終了前に最新main、PR/head/CI、正確な継続地点、未完了作業、失敗と証拠、次の具体的実行アクションをリポジトリへ保存する。次の毎時監視は実行停止を検出した場合だけ復旧する。
```

## Reporting

Reports must state what was actually changed and validated, exact PR/head/merge/main SHAs where relevant, retained failures/negative evidence, current unfinished work, exact next action, and the truthful AGI claim boundary. Do not claim a scheduler control-plane fact, model identity, external independence, delivery, or AGI status that was not actually verified.
