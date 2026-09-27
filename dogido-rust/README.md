# ドギド Rust版

ドギド本体を段階的に移植するための実装です。通常会話の試験、接続専用HTTPサーバー、RigのLLM接続試験、Python版との通信比較が動きます。

通常会話、戦闘・環境反応の判断、明示した剣への持ち替え、音声配送をRustで接続しています。会話材料、本文prompt、発話検査、読み補正は移行用Python補助を使います。自動川柳の情景音声・生成・検査・保存・掛け軸、意味相談・一行編集・検証付き修正案・採否・終了確認と、戦闘後の安全な再開、安定した単独敵が残る間の明示意思による再開も接続しています。読み訂正、指摘の参考保存、保存した句の検索、完成句の明示直し・自作句の保存、ローカル知識回答と限定国語対話も接続済みです。旧分類器へのfallback、宛先確認・保留入力、Webは後続段階です。進行は[移行計画](../docs/rust-migration-plan.md)を参照してください。

## ビルド

Rustは[rustup](https://rust-lang.org/tools/install/)で用意してください。`rust-toolchain.toml`でRust 1.98.1、`Cargo.lock`で依存を固定しています。Rigは`rig-core 0.42.0`です。

リポジトリのルートから実行します。

```sh
./dogido-rust/cargo.sh build --release --locked
```

`cargo.sh`は通常のRust環境のほか、`DOGIDO_RUST_TOOLCHAIN_DIR`で指定した場所の`cargo/`・`rustup/`を使えます。

## 一往復の会話を試す

既存のMLX API（既定8080）とVOICEVOXを起動した状態で、次を開きます。

```sh
./dogido-rust/start_dialogue.command
```

初回はreleaseビルドを行い、起動したターミナルに入力・各LLM呼出・音声・終了状態を表示します。起動後、`http://127.0.0.1:5056/rust-chat`で「ゲームなしで会話を試す」を押すと、テキスト入力から返答の音声まで確認できます。観測がないことを明示した試験で、Minecraftの状況を模造しません。

マイクも使う場合は、サーバー起動後に別のターミナルで次を開きます。

```sh
./dogido-rust/start_voice.command
```

既存のwhisper・VAD・WebRTC AECを再利用し、無音800msで発話を区切って5056へ送ります。**終了はそれぞれのターミナルでCtrl+C**。通常Python版のサーバー・マイク入力を停止してから試験してください。共有MLXやVOICEVOXエンジンの起動・停止は行いません。準備だけの確認には両ファイルの`--check`を使えます。録音やモデル生成は行いません。

起動ファイルはGitの共通ディレクトリから元チェックアウトを探し、その`.env`と`dogido-llm`を読みます。別の場所は`DOGIDO_RUST_SETTINGS_DIR`、Pythonだけは`DOGIDO_PYTHON`で指定できます。設定ファイル自体は変更しません。VOICEVOXのspeaker・平時の話速・pitch・volume・読み設定、chat routeのモデル・上限・timeoutを引き継ぎます。出力sampling rateの個別設定は未対応のため、指定時は理由を表示して停止します。

[実機チェック手順](manual-dialogue-check.md)にMinecraft接続と確認する発話をまとめています。

### この段階の動作

- `serve-dialogue`で明示選択する限定モードです。`serve`の接続専用動作は維持します。`/healthz`のreadyはRust受付の準備完了であり、モデル・TTSの応答成功を保証する値ではありません。
- 通常はplannerと本文を各1回。plannerの契約再試行と、不合格本文の言い直しはそれぞれ最大1回です。全体の補助処理は95秒まで。追加の分類モデルや音声の全件事前生成は使いません。
- 表示は生成中・音声準備中・再生中・完了・取消・失敗を区別します。入力受理後のuser履歴と、playerプロセスの成功終了後だけ確定するassistant履歴を、直近10件・各80字で保持します。音声無効時も再生完了へ偽装しません。
- 新しい入力、停止、session終了、危険な観測、観測の途絶で古い処理を取り消します。生成・再生を直列化し、所有するhelperとplayerを終了まで待ちます。モデルへのHTTP取消は共有MLXプロセス自体を停止しません。
- ゲーム接続時は時刻付きの10秒以内の観測が必要です。パニック時には警告を優先し、通常会話を止めます。敵の方角・距離・体数の質問と「静かにして」は、その会話停止判定より先に処理します。暗いという理由だけでは通常会話を一律拒否しません。
- テキスト入力はsessionを選べます。既存マイクの入力先はsessionが一つの場合だけ確定します。複数の接続があるときは余分な試験接続を終了してください。
- 音声は検査・読み補正済みの返答を文ごとに合成し、一文目の再生中に次の一文だけを先読みします。全ての文が再生を終えて初めて返答を完了にします。途中の失敗・取消で後続音声を捨て、全文を履歴に確定しません。LLM生成中の発声は未接続です。
- 必要な文だけを合成し、メモリに最大128件・32MB保持します。再生用WAVは再生終了・取消で削除します。会話表示は200件までで、再起動すると履歴・cacheは消えます。通常会話本文の長期保存は行いません。川柳と読み訂正の保存は後述します。

文分割は[voicevox-sentence-stream](https://github.com/yukincom/voicevox-sentence-stream)の日本語即時境界・180文字上限・末尾保持をRustへ移植しています。来歴とMITライセンスは[third-party](third-party/voicevox-sentence-stream/NOTICE)に保持しています。`audio_sentence_ready`に文ごとの合成時間、`audio_first_sentence`に音声準備から最初のplayer起動までの時間を出します。実際に耳へ届く時刻の計測とは区別します。

Python補助は`dialogue_helper.py`一件のstdio処理です。既存の通常雑談の材料・本文prompt・発話の採否・UniDicを再利用します。helperはgame-eventの判断、モデルHTTP、音声、保存を担当しません。これらの材料生成・検査・読みをRustへ移して同一入力比較を通した後に補助を外します。音声入力側のPython撤去は別段階です。

模擬LLM／TTS／playerによる実HTTPの取消・失敗・履歴・終了確認:

```sh
python dogido-rust/scripts/check_dialogue.py
python dogido-rust/scripts/check_sentence_audio.py
python dogido-rust/scripts/check_warnings.py
python dogido-rust/scripts/check_group_warnings.py
python dogido-rust/scripts/check_warning_observations.py
python dogido-rust/scripts/check_combat_runtime.py
python dogido-rust/scripts/check_environment_runtime.py
```

### 戦闘の移植範囲

`src/combat/`が通常・警戒・パニック・抑制・戦闘後の状態を管理します。通常敵の単体・群れ、音だけの敵、後方の警告、低体力、敵が動かない場面、敵対化した中立モブ、飛行敵、昼の雨・水中・燃焼、異世界到着時の群れを扱います。ウォーデン、ドラゴン、ウィザー、エルダーガーディアンの固有反応も接続しています。エンダーマンはFabricが敵対と観測した場合だけ戦闘対象になります。

「ゾンビどこ？」「敵は何体？」「ドラゴンどっち？」は現在の視認・方位・scan個体数からコードで即答します。未観測の名指し対象は別の敵へ置き換えません。再生待ちの質問・警告は最新の観測で再評価します。読み始めた回答や警告は、対象消失・方角・距離・体数が変わっても最後まで話します。次の質問には、その時点の観測で答えます。緊急警告・手動停止・観測期限切れ・接続終了は引き続き割り込めます。戦闘後の安堵は、先行する台詞が終わってから話します。

導火・ソニックブーム・新しい奇襲は低優先の生成や音声へ割り込みます。悲鳴と続く本文は一つの取消権限で再生し、同じsnapshotの反復で自身を止めません。「静かにして」による抑制中も新しい導火開始を優先します。名前入り「うしろ」は既存manifestの名前音源と語尾が両方あれば使用し、不足時は全文を合成します。群れの敵名・体数・語尾も全断片が揃う場合だけ使用します。

視認の正本と10秒の期限は全観測で更新します。音やambient通知の空の視認配列で、敵の消失を推定したり期限を延長したりしません。聴覚は独立した観測寿命を持ちます。発生済みの死亡・撃破・爆散への反応は、後続の空視認だけでは取り消しません。撃破根拠がない消失は離脱です。重複した結果や既報の死亡個体を、次の安堵で再び倒した扱いにはしません。

固定警告と位置・体数回答はLLMを呼びません。既存の死亡・安堵・遮蔽された敵音・昼の水中スケルトン・燃焼・deep_dark不穏音の言い回しだけ、元のprompt・検査をPython補助で再利用し、RustのRigから生成します。失敗時は既存固定文へ戻ります。判断・優先順・状態変更はRustが所有し、安堵では観測根拠より強い撃破主張を棄却します。短期の状況メモはコード事実8件・各80字までで、未再生の発話を会話履歴へ入れません。

暗所への入口、逃げるよう促す段階、呼吸、明るさ回復の安堵と、その最中の前方奇襲を接続しています。暗所の状態と再生中の音声を分け、戦闘へ移った後の反復観測で警告本文を止めません。川柳は危険中に保持して表示と時計をpauseし、安全な観測と警告の再生終了を待って再開します。

```sh
./dogido-rust/cargo.sh build --locked --offline --examples --bin dogido-rust
python dogido-rust/scripts/compare_threats.py
python dogido-rust/scripts/compare_combat.py
python dogido-rust/scripts/check_combat_runtime.py
```

`compare_combat.py`はPython正本と同じ系列で状態・台詞・cue・断片列を比較します。269系列の等値と、部分観測・保留・優先順など5系列の意図的変更を別々に確認します。結果は`reports/combat-parity.json`、模擬LLM・TTS・playerによるHTTP確認は`reports/combat-runtime-check.json`です。実マイク・スピーカー・Minecraft上の聞こえ方は[実機チェック](manual-dialogue-check.md)で確認します。

### 環境反応と剣への持ち替え

`src/environment/`が暗所・避難・夕方・雷・天候・ポータル・危険な光源、友好／中立モブ、建物・バイオーム・ホタル・匂い・照明器具の所持数変化を扱います。乗り物は乗車中だけ会話材料にし、独立した常時実況は追加しません。モブへの反応は会話後30秒の抑制を引き継ぎます。同じモブの反復cooldownとは別です。雷などの割込み後は受理済みの会話を再開し、未回答の同じ入力を履歴に重ねません。

環境の現在性は生成中・再生前にも照合します。現行Fabricの音／モブ通知に含まれる完全なworld/player sectionでは、建物・ポータル・乗り物等の不在も反映します。空の敵配列で視認を消したり、全観測の10秒期限を延長したりはしません。環境sectionを省略した通知は直前の全観測とその期限を使います。

読み始めた環境コメントは、明るさ・匂い・モブの在否などの変化だけでは途中停止しません。敵や被弾などの危険、雷の割込みは維持します。暗所の呼吸ループは例外で、明るさが戻ったら止めます。

`src/assist/`は明示した剣への持ち替えだけを扱います。定型の依頼はモデルなし、自然文は根拠付きの限定抽出です。原文・現在のslot・実行capability・命令期限・結果ACKをRustが検証します。音声の「県に持ち替えて」等の補正は音声入力に限ります。知識質問、引用、否定、過去の報告から操作せず、初回の失敗を成功として話しません。実行された結果はadapterのACKで確認します。

```sh
./dogido-rust/cargo.sh build --locked --offline --examples --bin dogido-rust
python scripts/compare_assist.py
python scripts/compare_environment_danger.py
python dogido-rust/scripts/check_environment_runtime.py
```

操作支援は同一入力1,572系列と意図的修正2系列、危険環境は91系列、ambientはPython由来202参照ケースで比較しています。地表の夕方警告を常に割込みにする差は危険環境比較器で明示的に正規化しています。命令期限切れの即時整理・成功未確認の断言抑止・未知結果への誤応答防止は等値比較とは分けています。統合試験は模擬LLM・TTS・playerによるものです。実ゲームの持ち替え、実モデルの表現、実スピーカーの間は実機確認が必要です。

既存モデルとVOICEVOXを使う明示的な試聴（このMacで音声が流れます）:

```sh
python dogido-rust/scripts/check_dialogue_live.py
```

この実試験はテキスト入力で行います。実マイク・Minecraftでの確認とは区別してください。試験結果は`reports/dialogue-check.json`、`reports/dialogue-live.json`に出力します。試験終了時に起動したサーバーと子プロセスを回収します。

## 接続専用サーバー

```sh
dogido-rust/target/release/dogido-rust serve
```

画面は`http://127.0.0.1:5056/dogido`です。終了はCtrl+C。既定は接続テスト用の5056ポートで、`--listen 127.0.0.1:0`なら空きポートを選び、起動時に表示します。この段階ではループバックアドレスにだけバインドできます。

`serve`はLLM・VOICEVOX・記憶storeを構築せず、外部サービスへアクセスしません。`.env`も読みません。Bearer認証が必要なら`DOGIDO_AUTH_TOKEN`を環境変数で指定します。トークンはログへ出しません。

| API | 現在の動作 |
|---|---|
| `/healthz` | サーバー稼働を返す。`dialogue_ready=false`、`llm_enabled=false` |
| `/api/v1/adapter-sessions` | 登録。heartbeat間隔5秒、batch上限25、schema `2026-05-24` |
| `/api/v1/adapter-sessions/{id}/heartbeat` | 接続更新とsequence記録。未知IDは404 |
| `/api/v1/adapter-sessions/{id}` | DELETEで終了。繰り返し終了も成功 |
| `/api/v1/display/snapshot` | 起動元、接続状態、登録／終了等の診断ログ。発言・資料はまだ空 |
| `/api/v1/haiku-workshop/snapshot` | 閉じたworkshopの既存形式。未知IDは404 |
| `/api/v1/game-events`・`/batch` | 不正データは422。検査後、古いsession IDは409、既知IDでも世界処理が未対応なので501 |
| `/api/v1/player-input` | 空入力・sessionなしは理由付きで拒否。それ以外も会話が未対応なので501 |
| 音声文脈・音声診断・記憶API | 未実装を501で返す |

501では`accepted=false`を返し、観測sequenceの消費・発話・操作・保存はしません。検査済みイベントの受信は接続表示の更新にだけ使います。重複判定と入力待ち列は独立した部品として実装済みですが、会話の消費先がない間はHTTP受付から使用しません。

受信検査は現行のフィールド・enum・範囲・ホットバー重複・匂いの意味制約を扱います。旧`peaceful_mobs`の互換名と、匂い以外の未知フィールドも保持します。整数は符号付き64bit、実数は有限の64bit浮動小数点で扱います。Pythonの任意精度整数や非有限値まで受理する契約にはしていません。

状態変更は一つの処理係へ直列に渡し、表示GETは公開済みsnapshotだけを直接読みます。HTTP側が待機を取り消しても、キューに入った登録／終了の順序と投影の公開は維持します。接続が途絶えた表示も定期更新します。

## LLM単独の接続確認

要求の形式だけを検査します。

```sh
dogido-rust/target/release/dogido-rust check dogido-rust/fixtures/connection.json
```

起動済みのMLXへ、一回だけ生成を依頼します。

```sh
dogido-rust/target/release/dogido-rust generate dogido-rust/fixtures/connection.json
```

既定の接続先は`http://127.0.0.1:8080/v1`、要求モデルはfixture内の`default_model`、タイムアウトは20秒です。接続先は`--base-url`、タイムアウトは`--timeout-ms`で指定できます。モデルの起動や切替は行いません。

このCLIは`.env`を読みません。認証が必要な接続先では`DOGIDO_LLM_API_KEY`を環境変数で設定します。キー未指定時は、RigのAPIに合わせて公開の仮値`dogido-local-no-key`をBearerヘッダーに送ります。

出力JSONには返答、`finish_reason`、入力／生成トークン数、要求／応答のモデル名、応答ID、API所要時間を残します。トークン数が返らなかった場合は`null`です。`elapsed_ms`は一回のAPI呼出と応答読取の時間で、音声の体感応答時間とは別です。

## 自動川柳の生成・検査を比較する

`src/haiku/`は準備済みの材料から三行を生成し、音数・文字種・出典・自然さを検査します。内容の再生成は不合格slotだけ、検査票の欠落は元の行を保持して一度だけ再検査します。再検査も読めなければ`grounding_unavailable`で終了します。出典は検査中だけ一時番号にし、結果には元IDと材料を保持します。検査の既定上限は512トークンで、句生成の上限と別です。

`haiku_response`は合否と出典の両方が完成した外側のJSON項目だけを回収します。説明末尾の途中切れを許容する一方、出典配列の途中にある一件を完全な検査票として拾いません。`haiku_bridge`はRig経由のchat／haiku routeと、prompt・読み補助の寿命を管理します。取消はwatchで通知し、呼出元は処理の完了まで待ちます。

```sh
./dogido-rust/cargo.sh build --locked --offline --examples
python dogido-rust/scripts/compare_haiku.py
python dogido-rust/scripts/compare_haiku_response.py
python dogido-rust/scripts/check_haiku_bridge.py
```

生成結果・要求・promptの67ケース、検査票解析4,875ケースを現行Pythonと照合します。模擬HTTPではroute、512トークン、元行の再検査、不合格行だけの再生成、通信失敗、取消・期限切れ時のhelper回収を確認します。比較基準のPythonは合否先行・番号付き材料・512トークンの修正を含む版が必要です。

生成単独の接続器は`examples/generate_haiku.rs`です。要求JSONに`input`（材料・制約）、`chat`と`haiku`（各`base_url / model / max_tokens / timeout_ms`）を指定し、`--python`で既存の依存が入ったPythonを選びます。接続先の省略による自動接続はありません。このCLIは生成部品だけを確認します。ゲーム中の発句・音声・初期workshop・JSONL保存は`serve-dialogue`へ接続済みです。

## ゲーム中の自動川柳

通常は10分周期で、安全かつ静かなsnapshot、または会話の返答を再生し終えた境界から始めます。生成された見どころを先に話し、同じ観測・材料で句を生成・検査します。掛け軸は生成中にthinkingを示し、完成してから三行を表示してworkshopの時計を開始します。新入力・危険・観測失効・停止では未完成の発句を取り消します。

起動設定は既存のchat／haiku別routeと、192トークンの生成・512トークンの検査を読みます。材料構築・辞書読み・promptは移行用Python補助、生成・採否・時計・保存・表示・音声の所有権はRustです。自動保存先は `.dogido_memory/rust-migration/sessions/<session_id>/` で、既存の記憶と分けています。完成句はその後の音声失敗でも残し、再生済みの会話とは区別します。

現在のworkshopは意味の相談、読み・音数・出典の確認、三行の表示と終了に対応します。検査はRustで行い、結果を見て説明する一手だけを追加できます。句の相談は実再生済みの直近4往復を独立して保持します。未採用案がなければ「終了」「終了でいいよ」や「いい句だね」などの明確な称賛はモデルを使わず、返事の音声より先に閉じます。返事が敵に遮られても再開しません。自然な終了も原文の意思を検証します。

「上五を『さくらいろ』にして」のようなプレイヤー指定の一行編集に対応します。未採用案を掛け軸へ別表示し、他の行も続けて直せます。「採用して」で元句と修正履歴を保存して現在句へ昇格し、「却下して」で案だけを戻します。未採用案がある時の「終了」は採否を確認します。採用・却下と終了をまとめて明示することもできます。保存に失敗した場合は元句と案を残します。

完成した直しは `直し: はるのいろ / さくらのはみる / あさひかる`、相談中なら `こう直して: はるのいろ / さくらのはみる / あさひかる` で保存できます。元句と直しを保存してから相談を閉じます。自作句は `川柳保存: 本文`、直前のドギドの句は「今の句を保存して」で保存します。直前の句が自動保存済みなら重複保存せず、未採用案も採用しません。これらの明示経路にはLLM判定を追加しません。

プレイヤーが全文を渡す保存では音数に合わせて内容を改作しません。全文の直しは表記と辞書で確定した読みを保存し、読みを確定できない本文は原文のまま記録します。自作句の保存は渡された本文を記録します。`直し:` と `川柳保存:` は旧版の一行・二行形式も受け付け、四行以上は省略せず形式を確認します。相談中の自然な全文直しは三行を必要とします。引用・伝聞中の `直し:` や「保存しないで」「保存していい？」は確定保存として扱いません。

行名の代わりに「さくらのはをさくらいろに変えて」のように現在の句本文を読んでも指定できます。既存の辞書で「桜の葉」等の漢字を読みへ戻し、現在の三行（未採用案がある場合はその案）に一意に一致する場合は、LLM判定を追加せず編集候補を検査します。対象や置換語が欠ける場合、複数行に一致する場合、否定・引用・疑問・条件を含む場合はこの確定経路を使いません。音数・元句との一致・明示採用は従来どおり確認します。

「中七を自然な表現に直して」のような修正依頼では、ドギドが対象行の案を生成し、日本語・意味・音数・発句時のhard制約・出典の重複を検査します。最大2候補までで、同じ不合格案を再検査しません。検査の途中切れは検査だけを取り直し、判定できなければ元句を保ちます。合格案は未採用として提示し、「その案で」「採用して」で初めて保存します。AIの日本語検査は誤判定することがあり、合格は品質保証ではありません。

意味説明を最後まで再生した後の「なるほど」には、句の話を終えるかを確認します。その確認も再生完了してから「うん」で終了、「まだ続けたい」で相談へ戻ります。代表的な短文にはモデルを呼ばず、自然な表現は同じ相談stepで判断します。途中停止・再生失敗・戦闘・新しい話で古い確認への同意を持ち越しません。未採用案がある時はこの終了確認を使わず、明示した採否を待ちます。

戦闘後は警告・安堵を先に話し、保持していた三行を再掲します。定型の「続ける？」は付けません。最後まで再生してから「うん」で相談へ戻り、「続けない」で終了します。未採用案がある場合は再掲もその案を使い、再開への同意では採用せず、終了時も採否を確認します。再掲中の新しい質問にはそのまま応じ、敵が来た時や観測が古い時は再掲を止めて句を保持します。音声失敗時は間を置いて安全を再確認します。

同じ単独敵が3ブロックより遠く、接近や直近被弾がなく8秒以上安定した場合は、「句の続きを話そう」などの意思を確かめて相談を再開できます。自動では再開しません。敵の接近・被弾・個体や数の変化では再び中断します。中断中の入力だけをOS AI優先の五分類へ渡し、使えない場合はchat routeと既存の閉じた規則へ戻します。通常会話にはこの分類を追加しません。設定は既存の `DOGIDO_PLATFORM_AI_PROVIDER` 等を引き継ぎ、自動モデル取得は既定offです。

読み訂正は「読み: 草地=くさち」「草地の読みはくさち」の明示形をモデルなしで保存します。「草地はくさち」の省略形は、相談中なら現在の材料のラベルと一致する場合だけ優先し、句の変更提案を横取りしません。「そうちじゃなくてくさち」の全かな訂正は、現在の既知バイオーム名へ対応させます。引用文中の訂正を保存せず、バイオーム不明なら語名を確認します。現在句・未採用案は変更しません。

読みの保存先は設定した移行用記憶ルートの `long_term/catalog_corrections.jsonl` です。接続IDが変わっても同じルートから再読込し、次の発句に正しい読みと禁止する誤読を渡します。この辞書はその記憶ルートを使う接続で共有し、人物別の記憶分割は追加していません。既存Pythonでも読める追記形式で、元のPython版の記憶へは書き込みません。保存失敗時は成功を答えず、記憶無効時は読み書きしません。保存・受付・取消はRust、カタログと辞書への反映は既存Python補助を使います。

句への指摘は、検証済みの相談結果から `long_term/haiku_critiques.jsonl` と `haiku_lessons.jsonl` に保存します。次の発句では最大3種類の注意を参考として渡し、禁止語にはしません。14日より古い注意、またはその後6句以上が保存された注意は参照しません。「いい句」の称賛は注意を消さず、「気にせんで」「前の注意はもういらん」で明示的に緩めます。修正案を求める依頼だけでは新しい注意を増やしません。

「覚えてる句を教えて」「今日の句」「雪原の句を思い出して」で、同じ記憶設定の保存済みの句と採用済み修正を検索し、最大2件を返します。設定ルート直下の既存JSONLと `sessions/<接続ID>/long_term/` が対象です。現在地だけには限定せず、指定した場所・保存日時で探します。条件に合わず別の句を返す場合は、その旨を伝えます。未採用案を検索結果へ加えず、相談中の現在句や案も変更しません。検索と明示的な注意の解除はLLMを呼びません。

OS SDK・prompt・JSON契約と発話根拠の検査・短文照合・場所と日付の日本語解釈・辞書読み・既存の行制約は一時的なPython補助を残しています。未採用案、現在句との一致検査、採否、記憶の保存・検索・期限判定、相談段階と再生完了の照合はRustで処理します。

相談段階の検証:

```bash
python dogido-rust/scripts/compare_workshop_inspection.py
python -m pytest dogido-rust/scripts/test_workshop_helper.py -q
python dogido-rust/scripts/check_workshop_runtime.py
python dogido-rust/scripts/check_workshop_edits.py
python dogido-rust/scripts/check_workshop_revision.py
python dogido-rust/scripts/check_workshop_followup.py
python dogido-rust/scripts/check_workshop_combat.py
python dogido-rust/scripts/check_workshop_provisional.py
python dogido-rust/scripts/check_reading_runtime.py
python dogido-rust/scripts/check_memory_runtime.py
python dogido-rust/scripts/check_poem_input.py
python -m pytest dogido-rust/scripts/test_memory_query.py -q
python -m pytest dogido-rust/scripts/test_combat_input_helper.py -q
```

実モデルの相談だけを試す場合は、起動済みの接続先を明示します。観測・句・音声は模擬で、実ゲームやスピーカーは使いません。このコマンド自体はMLXを起動・停止しません。

```bash
python dogido-rust/scripts/check_workshop_revision_live.py --base-url http://127.0.0.1:8080/v1 --model mlx-community/Qwen3.6-35B-A3B-4bit-DWQ --output .dogido_tmp/workshop-revision-live.json
```

```sh
python dogido-rust/scripts/test_haiku_preparation.py
python dogido-rust/scripts/compare_haiku_record.py
python dogido-rust/scripts/check_haiku_runtime.py
```

最後の検証は空きローカルポートの模擬モデル・TTSと無音playerを使い、終了時に所有プロセスを回収します。実モデルの品質・速度とMinecraft・音声実機の確認は別途必要です。

## ローカル知識回答・限定国語対話

workshop外の明示的な知識質問は正本DBから答え、参考資料を発話と一緒に表示します。国語の質問と学習中の続きは、問いの解釈を1回、必要な場合だけ資料に基づく説明を1回生成します。漢字の配当学年・明示かなの音数は表・計算の結果を使い、対象や読みが不明なら聞き返します。通常雑談には国語分類を追加しません。

聞き返しの状態とassistant履歴は、音声を最後まで再生した後だけ確定します。学習中は川柳時計と非敵対ambientを止め、戦闘後はプレイヤーが明示した場合に続きを扱います。Web検索とブラウザー起動はまだ接続していません。

既存の開発用Python環境で検証できます。HTTP試験は模擬モデル・模擬音声を使い、所有プロセスを終了時に回収します。

```sh
./dogido-rust/cargo.sh build --locked
python -m pytest dogido-rust/scripts/test_language_helper.py tests/test_language_dialogue.py -q
python dogido-rust/scripts/check_knowledge.py
python dogido-rust/scripts/check_foreground_runtime.py
python dogido-rust/scripts/check_language_runtime.py
```

## 通常会話plannerの単独確認

```sh
dogido-rust/target/release/dogido-rust plan-chat dogido-rust/fixtures/planner/explicit_repair.json
```

本人の訂正、聞き返し、会話継続、現在観測への照合など一件のread actionを選びます。現在の発話と実再生済み履歴に根拠があるかをRustで検査し、AIに履歴・世界・保存の変更権限を渡しません。引用内の訂正語や原文にない置換語は採用しません。対象候補と現在観測の照合、および未観測時の固定返答も独立関数として移植しています。

入力は状態機械が作る読み取り用の`PreparedPlan`です。現段階では現行Pythonが作った文脈・カタログ由来のfallbackを合成fixtureに書き出して使います。文脈の収集・カタログ検索・通常返答の生成はまだ移植していません。単独CLIからは発声しません。`serve-dialogue`では同じplannerを移行用Python補助と接続しています。

現行の日本語prompt、温度0、上限640、thinking指定を維持します。JSON契約違反時だけ再試行1回、低信頼・初回のJSON途中切れ・通信失敗では追加呼出しません。型エラーの診断名はRust用ですが、発話根拠・routing違反の診断と再試行の条件はPythonと同じです。各試行の本文・終了理由・トークン数・API時間をreportへ残します。

起動済みモデルで6件を逐次確認するには、次を実行します。実モデルの使用が許可された環境でのみ実行してください。

```sh
python dogido-rust/scripts/check_planner_live.py
```

2026-09-21の確認では、会話継続・本人訂正・聞き返し・引用語・未観測の猫への問い・聞き返し後の説明の6件が各1回で採用されました。API時間は1,568〜3,284ms、全件`finish_reason=stop`。要求・応答モデル名とも`default_model`で、具体的なモデル名は応答から確定できません。合成文脈でのplanner検証であり、返答文・STT・TTSを含む応答時間や速度改善率ではありません。

## 検証

```sh
./dogido-rust/cargo.sh test --locked
./dogido-rust/cargo.sh clippy --locked --all-targets -- -D warnings
```

plannerのprompt・採否・訂正原文・現在観測をPythonと比較します。通信試験は一時ポートの模擬サーバーを使い、終了時に必ず回収します。

```sh
./dogido-rust/cargo.sh build --locked --examples --bin dogido-rust
python dogido-rust/scripts/compare_planner.py
python dogido-rust/scripts/check_planner_transport.py
```

同一入力比較2,145件、実HTTPでの再試行・失敗確認12件。結果は`reports/planner-comparison.json`と`reports/planner-transport.json`です。実再生完了履歴の投影を入力にしており、実際の音声完了イベントや履歴更新の配線はこの比較に含めません。

Python側のplanner定型文や比較fixtureを変更した場合の再生成は以下です。これらの生成物を使うRust実行時にはPythonを呼びません。

```sh
python dogido-rust/scripts/generate_planner_prompts.py
python dogido-rust/scripts/planner_cases.py
```

既存Python環境で比較器を実行します。先に通常の開発用ビルドを作ってください。

```sh
./dogido-rust/cargo.sh build --locked
python dogido-rust/scripts/compare_provider.py
```

比較器は同じリポジトリのPythonプロンプト・providerと既存fixtureを使い、一時的なループバックHTTPサーバーへRust版を接続します。送信本文、返答、終了情報、失敗時の呼出回数を比較し、サーバーを終了して`reports/provider-comparison.json`へ結果を保存します。モデル呼出や音声再生はありません。

終了情報を返さない旧Python版では、その項目だけfixtureと照合します。Pythonとも比較できたケースは`python_metadata_comparisons`に記録します。

接続サーバーの実HTTP・再起動・終了確認は、releaseビルド後に実行します。

```sh
python dogido-rust/scripts/check_server.py
```

一時ポートでRustサーバーを起動し、Pythonの型と閉鎖HUDの投影を照合します。再起動後の404／409と再登録、Ctrl+C／SIGTERMでの終了を確認し、結果を`reports/server-check.json`へ保存します。試験プロセスは終了時に回収し、実モデル・TTSは呼びません。

イベントの採否・既定値・拡張項目、重複判定、入力保持をPythonと比較します。ネットワーク接続はありません。

```sh
./dogido-rust/cargo.sh build --locked --example check_inputs
python dogido-rust/scripts/compare_inputs.py
```

結果は`reports/input-comparison.json`へ保存します。入力待ち列の比較は本文正規化後の保持・順序までで、STT補正、会話への振り分け、危険時の保留判断、音声中断は含みません。

Python側の受信モデルを変更した場合は、宣言を再生成し、意味検証と比較結果を確認します。生成後のRust実行にPythonは不要です。

```sh
python dogido-rust/scripts/generate_event_types.py
./dogido-rust/cargo.sh fmt
```

## 接続実装の範囲

- RigのChat Completions経路を明示し、messages・温度・トークン上限・thinking指定を保持します。
- messagesは既存Pythonと同じrole／文字列形式に揃えます。通信の自動再試行とリダイレクトは無効です。
- 生成本文が不完全なJSONでも、`finish_reason=length`と本文・トークン数を上位へ返します。内容の検査・採否・再生成はドギドの処理が担当します。通常会話plannerと自動川柳の生成・検査ループは移植済みです。ゲーム中の自動発句・情景音声・保存・掛け軸も接続済みです。
- 対象は現在のMLXの通常のChat Completions応答です。Rigは`id`・`model`・messageの`role`等を要求するため、旧Pythonが許容する省略形すべての互換実装ではありません。
- LLM境界、session／heartbeat／player-inputの外形、ゲームイベントの受信モデル、通常会話plannerの型を移植しています。句の自動保存、採用済みrevision、全文の明示直し・自作句、critique・lesson・読み訂正の形式はPythonと照合済みです。設定全体、旧分類器fallback、workshop行動記録などは引き続き移植中です。
