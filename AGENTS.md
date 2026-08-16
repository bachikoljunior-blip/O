# Agent Instructions

このリポジトリは自己改善ランタイム自体です。

1. 会話履歴を永続状態の正本にしない。`.continual/runs/` と `.continual/episodes/` を正本にする。
2. 意味判断をPython固定コードへ追加しない。判断は `prompts/` のcomponentまたはそのCandidateへ置く。
3. 新しい専門Skillを先回りで大量作成しない。Episodeから必要性が出た時にCandidate化する。
4. Candidateを生成直後にglobal activeへしない。影響execution unit直前にpre-application、実行後にpost-result、scope単位採用。
5. active Candidateも反証で降格可能にする。
6. Local Learnは各意味的実行の終わりに行う。Learn自身だけは再帰Local Learnしない。Post-Task Learnは必ず別途残す。
7. 内部思考全文を保存しない。目的、入力、行動、重要判断、観測、証拠、成功/失敗、未解決、再利用発見だけ残す。
8. push/merge/公開/送信/削除などはside-effect IDと前後状態を保存し、再開時は外部状態確認なしに再実行しない。
9. APIキーや秘密情報をコミットしない。
10. `NO_CHANGE` は正常な学習結果。
