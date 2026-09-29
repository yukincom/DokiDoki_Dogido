# 川柳の出典と相談用文脈の Rust 投影

`haiku::source_atoms` は、渡されたカタログ項目・観測・検証済み見どころから出典を作り、
保存済み materials の出典を読み戻す純粋部品。`haiku::SourceAtom` を共用し、
カタログの選定・現世界の観測・保存は行わない。保存済み出典を読む際に、現在のカタログへ
問い合わせて文言を更新しない。一次材料と詩的解釈の claim、派生元、重複、共有可否も検査する。

`workshop_projection` は現在句・未採用案・実再生済みの短い対話・保存済み材料から、
相談の details と editor の revision input を構築する。表示と読み、現在句と未採用案を分離し、
実行直後の step は直近履歴と二重に提示しない。source atom24件、出典3件、履歴4往復、
直近step6件、現在turnのstep4件など、正本Pythonの上限と並び順を維持する。

`Snapshot::from_view` は既存helperへ渡す検証済みviewの投影を再現する。
元helperが転写しない last_findings / last_repair_feedback は勝手に補わない。
直接 `Snapshot` を構築する場合は両フィールドを保持でき、修正結果は元句が一致する場合だけ載せる。
短い同意は `fixed_followup(text, Stage, pending)` で候補actionを返すだけ。
実再生後の段階・pending・終了・採否の更新は従来のRust runtimeが所有する。

workshop runtime の相談 details・短い同意・editor入力はこのAPIへ接続済み。
`prepare_details` / `fixed_followup` / `revision_input` のPython操作は実行経路から外し、
互換比較用に定義を残す。相談と修正の出典は保存済み materials だけから組み立てる。

局所編集が許可された `decide` 段階だけ、同じhelper子へ `fragment_candidate` を一度送り、
既存の辞書依存の固定編集候補を得る。検査後・修正検証後・編集禁止の相談にはこのIPCが無い。
候補は元のRust契約・原文evidence・CAS検査へ戻し、helper自身に採用権限を渡さない。
モデル呼出数、全体95秒上限、取消、子回収は維持する。native投影にも旧IPCの100万byte上限を適用する。

knowledge routeの句との照合、固定・自然な局所編集の辞書変換、三行の読み確定は今回の範囲外。
新たなhelper子・辞書・モデル呼出は追加していない。既存の読み・音数・保存出典の `inspect` はRustで実行する。

Python正本由来で出典1,831例、相談投影408例、短い同意5,712例を比較する。
相談投影は全項目に加えPython互換のJSON文字列も比較し、promptのキー順・空白を維持する。
生成器は辞書の初期化を禁止している。実モデル・音声・Minecraft確認を代替するものではない。

```sh
python dogido-rust/scripts/generate_workshop_source_fixtures.py
./dogido-rust/cargo.sh test --offline haiku:: --lib
./dogido-rust/cargo.sh test --offline workshop_projection --lib
./dogido-rust/cargo.sh test --offline workshop_followup --lib
```

実配線の回帰検査は、canonical準備580例の全項目比較、固定編集アダプターのPython詳細・prompt・検査再呼出禁止、
編集候補だけのIPC、IPC不要段階、旧helper応答の拒否、サイズ上限、取消と実子回収を含む。
模擬HTTPの `check_workshop_runtime.py` / `check_workshop_edits.py` /
`check_workshop_revision.py` / `check_workshop_records.py` は一時ポートと所有プロセスだけを使い、
実モデル・実音声を使わず、終了時に子を回収する。
