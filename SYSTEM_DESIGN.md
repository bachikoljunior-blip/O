# System Design

## 固定される最小核

改善対象から外すのは、意味判断をしない以下だけです。

- atomic JSON write
- append-only event追記
- UUID生成
- revision競合検出
- 選択済みcomponent versionのロード
- side-effect stateの保存
- `continue / finished / blocked` の機械的ループ
- 現在のPreflight Evaluatorを起動する最小bootstrap

現在実行中のPreflight Evaluatorは自分自身を途中交換しません。Evaluator Candidateは次回以降のinvocationでtrialします。現在実行中Runnerも途中交換せず、新版は次runからです。

## ファイルと実行順

### 1. `continual start`
読む: `prompts/entry.md`, `.continual/system/active-components.json`。
作る: `.continual/runs/<run_id>/request.md`, `events.jsonl`, `snapshot.json`。
ENTRY実行前に `prompts/candidate_evaluate.md` のpre-applicationを通し、ENTRY versionを選びます。ENTRY本処理後、同じmodel callの終了フェーズでLocal Learnを行い、`local-learn/` と `fragments/` を保存します。

### 2. Runner選択
Runnerは薄いPythonループです。ただしRunner方式自体のCandidateは次run開始前Preflightで選べます。現在run途中では交換しません。

### 3. Root前Preflight
読む: `snapshot.json`, `active-components.json`, Candidate indexのうちRoot関連metadata。
実行: `candidate_evaluate` pre-application。
保存: `preflight/root-<n>.json`。
その後 `prompts/root.md` またはCandidate versionをfresh API callで実行します。

### 4. Root
Rootは1ステップだけ判断し `execution-units/<id>.json` を作ります。全Episode/全Skill/巨大ログを読みません。本処理後、同一応答内の `local_learn` フィールドでLocal Learnを返し、fragment保存後にcontextを終了します。

### 5. execution unit直前Preflight
Execute、子Skill、親再開、Task Evaluate、Consolidate Episode、Post-Task Learnなど、意味的componentの**実行直前**にだけ関連Candidateを評価します。

pre-applicationは以下を決めます: applicability、active/candidate version、trial scope、Candidate競合/依存、baseline要否、必要証拠、rollback、コスト観点。

### 6. Execute
`prompts/execute.md` をfresh call。専門Skillがなければ一般推論で処理します。終了直前にLocal Learnし、Experience Fragmentを保存します。子Skillが必要なら親継続状態を永続化して終了し、子を別execution unitとしてfresh実行します。

### 7. Candidate post-result
局所証拠で十分ならunit直後。Task全体の証拠が必要ならTask Evaluate後まで保留します。`active-for-scope` になっても支持・反証証拠を継続追記し、将来降格可能です。

### 8. Task Evaluate
`prompts/task_evaluate.md`。自己申告より、test/build/lint/git/API/成果物/ユーザー成功条件などの外部証拠を優先。FAILならfresh Rootが修正unitを作ります。終了直前Local Learnあり。

### 9. Consolidate Episode
`prompts/consolidate_episode.md`。

`Experience Fragment群 -> Consolidate Episode -> Task Episode`

順序、重複、成功条件と結果、Evaluator、Candidate trial、証拠、環境、未解決事項を統合します。欠損Contextを推測しません。終了直前Local Learnあり。

### 10. Post-Task Learn
`prompts/learn.md`。今回・最近・類似・長期Episodeから必要範囲を自分で選びます。Local Learnは再帰呼出ししません。`NO_CHANGE` は正常です。新SkillだけでなくRoot/Evaluator/Learner/Episode/Context/Runner/構成/モデル選択等もCandidate化できます。

## Candidateの追加重要仕様

Candidateは `depends_on`, `conflicts_with`, `tested_with`, `incompatible_with`, `supersedes` を持てます。既存Skill/Candidate/rejected案との重複をLearnが必要範囲で確認します。

評価では必要に応じて quality, latency, token/context cost, tool calls, external cost, failure risk を比較します。重みは固定しません。

`active-for-scope` は supporting/contradictory evidenceを蓄積し、`candidate` や `rejected-for-scope` へ戻せます。

環境差による誤因果を減らすため、fragment/episodeには model、runner version、Python、OS、repository commit、active component versions を記録できます。

ユーザーがTask完了後に否定/肯定feedbackを返した場合、`relations.jsonl`へappendし、将来LearnとCandidate Evaluateが参照できます。

システム中枢Candidateでは、必要とEvaluatorが判断した時だけ少数の代表Episode/Taskを回帰確認に選べます。全過去Task総当たりは禁止しませんが初期既定にはしません。

CandidateをNOT_APPLICABLEにした後で失敗やuser feedbackから適用すべきだったと分かった場合、`misclassified_applicability` 証拠としてEvaluator自身の改善材料にします。
