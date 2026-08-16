# Continual ChatGPT

ChatGPT / OpenAI Responses API を実行主体にした、最小の継続学習・自己改善ランタイムです。

## 目的

1件の依頼を `実行 → 評価 → Experience Fragment → Task Episode → Learn → Candidate` まで閉じ、Candidate は**その候補が実際に影響する execution unit の直前だけ**評価します。Candidate の採用は scope 単位です。

各意味的コンポーネント（ENTRY / Runner選択 / Root / Execute / Task Evaluate / Consolidate Episode / Learn / Candidate Evaluate / Context方式 / Episode方式 / モデル選択等）は Candidate 化できます。固定コードは atomic write、event append、ID、revision、外部副作用状態、選択済みversionの機械的起動などに限定します。

## 重要な学習規則

- Rootを含む改善対象は、**実行前Preflight**を通してから起動する。
- 各意味的実行は本処理終了後、同じContextが残っている間に `Local Learn` を行い、その後 Experience Fragment を保存する。
- Learn 自身は再帰 Local Learn を呼ばない。
- Task終了後の `Post-Task Learn` は残し、今回・最近・類似・長期Episodeを必要範囲だけ読む。
- `Experience Fragment -> Consolidate Episode -> Task Episode`。Consolidateは失われたContextを推測しない。
- Candidateは新規依頼時に一括評価せず、影響箇所の直前だけ評価する。
- Candidate Evaluate は pre-application / post-result の2段階。
- active-for-scope も永久確定ではなく、反証で降格可能。
- rejected理由、Candidate競合/依存、重複、コスト、環境差、ユーザーフィードバック、証拠の出所を保存できる。
- Learn の正常結果として `NO_CHANGE` を認める。

## セットアップ

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
export OPENAI_API_KEY='...'
export OPENAI_MODEL='gpt-5.6'   # 任意。未指定時もこの初期値
```

OpenAI APIキーはリポジトリへ保存しないでください。

## 実行

```bash
continual start "ユーザー依頼"
```

中断後:

```bash
continual resume <run_id>
```

ユーザーフィードバックを過去Episodeへ関連付ける:

```bash
continual feedback <episode_id> "使いにくかった。理由は..."
```

## ディレクトリ

- `prompts/` 意味的判断を行う各コンポーネントの指示。Candidateで差し替え可能。
- `src/continual/` 薄い実行・永続化ランタイム。
- `.continual/runs/` runの正本。会話履歴は正本ではない。
- `.continual/episodes/` Consolidated Task Episode。
- `.continual/candidates/` Candidate、scope証拠、reject履歴。
- `.continual/system/` active component pointer と schema/version。

詳細な制御フローは `SYSTEM_DESIGN.md` を参照してください。
