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
互換比較用oracleに定義を残す。相談と修正の出典は保存済み materials だけから組み立てる。

意味説明では、句の言葉・響きと当時の材料を手がかりに、情景や感情へつながる解釈を返す。
材料と語句の一対一対応は要求せず、比喩や連想を膨らませてよい。なじみのない語も
意味質問だけで失敗扱いせず、今の読みとしてもっともらしい説明ができれば受け入れる。
未知語の辞書的定義や、記録にない生成時の本心は事実として断言しない。
説明しにくい語は、ドギドが「噛んだ」ことにしてよい。自分の言い間違いとして引き受け、
言いたかった情景や気持ちを普通の言葉で説明する。比喩を一律に言い間違い扱いにはしない。
説明から句本文・出典・採否を変更せず、発句時の日本語・音数・意味保持の検査も変更しない。

`scripts/check_workshop_meaning_live.py` は保存句5種類と、過去説明の再解釈・材料記録なし・
普通の比喩・説明への反論後を含む11ケースを、指定した既存モデルへ送る任意の独立テキスト検査。
Rustとgolden比較しているPythonのprompt・validatorを使い、ゲームのsessionや記憶は変更しない。
回答例は入力せず、外形の通過と意味内容の合否を分ける。報告の `contract_passed` は前者だけで、
後者は `review_criteria` に従い、言葉や材料から情景・気持ちへのつながりが納得できるか確認する。

```sh
python dogido-rust/scripts/check_workshop_meaning_live.py \
  --model MODEL_ID --output /tmp/workshop-meaning.json
```

2026-09-30のQwen3.6-35B-A3B-4bit-DWQによる検査では、9件とも初回で `explain` として
契約を通過し、findingと状態変更はなかった。内容評価は7件合格、2件未合格。
「ふゆむ」の漢字表記の断定と、「ひるべ」を夕暮れへ取り違える説明が残った。
Pythonの関連1,863件とRustの相談prompt・validation10件を確認済み。
少数の独立テキスト検査であり、実Minecraft・実音声の検証や説明品質全般の保証ではない。

「噛んだ」の許可後にも元の9件と反論後2件を実モデルで検査したが、この逃げ道を使う返答は
確認できなかった。語義を断定する返答が残るため、指示追加による改善は未確認。

句と材料の照合、固定・自然な局所編集、三行の読み確定は `workshop_editing` が担当する。
原文と解釈、行指定とfragmentの一意性、未採用案、保存済み出典、表示と読みを分離したまま、
候補を既存の音数・hard制約・原文evidence・CAS検査へ戻す。採用・保存の権限はruntimeに残す。
完成三行の持込みは従来どおり音数外の句も保管でき、読みが確定しない場合は推測しない。

漢字の中立読みが必要な場合だけ、既存の同じhelper子へ `tts_tokens` を送る。
Rust側でかな変換し、同一turnの同じ読み要求をcacheする。辞書なし・辞書内部の失敗は元表記へ戻し、
IPC破損・schema不一致・取消・期限超過は成功へ変換せず子を回収する。漢字なしの照合・編集はIPC不要。
`workshop_helper.py` は辞書SDKトークン取得だけを担当し、従来ロジックはオフライン比較専用の
`workshop_oracle.py` に分けている。カタログの読み上書きはこの中立読みへ混ぜない。

戦闘中断中の固定fallbackも同じRust部品へ接続し、OS AIの分類だけをSDK workerへ残す。
相談中は句の入力を優先し、匂い・戦況質問・持ち替え語が含まれても、敵の根拠がない間は
先行する固定応答へ渡さない。匂い、友好Mob、照明、暗所、雷、夕方、火元などの環境発話も
相談中は停止する。暗さや敵の根拠がない被ダメージだけでは句を戦闘中断にしない。
視認・聴覚・敵数・戦闘継続の観測がある場合は従来の戦闘中断と復帰を使い、
死亡・ディメンション変更・観測失効の停止も維持する。相談終了後は現在観測から通常の反応へ戻る。
開始済みの敵警告は対象が消えても完走し、戦闘結果・安堵の後で句へ戻る。
音声とイベント内テキストの両入口、環境による中断抑止、暗所での復帰、終了後の通常応答は
`check_workshop_focus.py` の模擬HTTP・モデル・音声で検査する。実Minecraft・実音声は未確認。
モデル呼出数、workshop全体95秒・完成三行の15秒上限、取消、epochと子回収は維持する。
native投影にも旧IPCの100万byte上限を適用し、新たなhelper子・辞書・モデル呼出は追加しない。
読み・音数・保存出典の `inspect` もRustで実行する。

Python正本由来で出典1,831例、相談投影408例、短い同意5,712例を比較する。
相談投影は全項目に加えPython互換のJSON文字列も比較し、promptのキー順・空白を維持する。
生成器は辞書の初期化を禁止している。実モデル・音声・Minecraft確認を代替するものではない。

```sh
python dogido-rust/scripts/generate_workshop_source_fixtures.py
./dogido-rust/cargo.sh test --offline haiku:: --lib
./dogido-rust/cargo.sh test --offline workshop_projection --lib
./dogido-rust/cargo.sh test --offline workshop_followup --lib
```

実配線の回帰検査は、canonical準備580例と編集・材料2,666例、元のprompt・検査の比較、
同一辞書子の再利用、辞書失敗時の元表記、IPC不要の入力、旧helper応答の拒否、
サイズ上限、取消・期限超過と実子回収を含む。
模擬HTTPの `check_workshop_runtime.py` / `check_workshop_edits.py` /
`check_workshop_revision.py` / `check_workshop_records.py` / `check_poem_input.py` /
`check_knowledge_handoff.py` / `check_workshop_provisional.py` は一時ポートと所有プロセスだけを使い、
実モデル・実音声を使わず、終了時に子を回収する。
