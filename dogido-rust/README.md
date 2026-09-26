# ドギド Rust版

ドギド本体を段階的に移植するための実装です。通常会話の試験、接続専用HTTPサーバー、RigのLLM接続試験、Python版との通信比較が動きます。

通常会話、戦闘・環境反応の判断、明示した剣への持ち替え、音声配送をRustで接続しています。会話材料、本文prompt、発話検査、読み補正は移行用Python補助を使います。自動川柳の情景音声・生成・検査・保存・掛け軸も接続しています。句の共同編集、記憶の検索、国語・Webは後続段階です。進行は[移行計画](../docs/rust-migration-plan.md)を参照してください。

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
- 必要な文だけを合成し、メモリに最大128件・32MB保持します。再生用WAVは再生終了・取消で削除します。会話表示は200件までで、再起動すると履歴・cacheは消えます。長期記憶の読み書きはありません。

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

「ゾンビどこ？」「敵は何体？」「ドラゴンどっち？」は現在の視認・方位・scan個体数からコードで即答します。未観測の名指し対象は別の敵へ置き換えません。再生待ちの質問は最新の観測で再評価します。読み始めた方向回答は、同じ対象・方角で3ブロック以内の概算距離変更だけなら最後まで話します。対象消失や方角変更は古い回答を取り消して再照合します。

導火・ソニックブーム・新しい奇襲は低優先の生成や音声へ割り込みます。悲鳴と続く本文は一つの取消権限で再生し、同じsnapshotの反復で自身を止めません。「静かにして」による抑制中も新しい導火開始を優先します。名前入り「うしろ」は既存manifestの名前音源と語尾が両方あれば使用し、不足時は全文を合成します。群れの敵名・体数・語尾も全断片が揃う場合だけ使用します。

視認の正本と10秒の期限は全観測で更新します。音やambient通知の空の視認配列で、敵の消失を推定したり期限を延長したりしません。聴覚は独立した観測寿命を持ちます。発生済みの死亡・撃破・爆散への反応は、後続の空視認だけでは取り消しません。撃破根拠がない消失は離脱です。重複した結果や既報の死亡個体を、次の安堵で再び倒した扱いにはしません。

固定警告と位置・体数回答はLLMを呼びません。既存の死亡・安堵・遮蔽された敵音・昼の水中スケルトン・燃焼・deep_dark不穏音の言い回しだけ、元のprompt・検査をPython補助で再利用し、RustのRigから生成します。失敗時は既存固定文へ戻ります。判断・優先順・状態変更はRustが所有し、安堵では観測根拠より強い撃破主張を棄却します。短期の状況メモはコード事実8件・各80字までで、未再生の発話を会話履歴へ入れません。

暗所への入口、逃げるよう促す段階、呼吸、明るさ回復の安堵と、その最中の前方奇襲を接続しています。暗所の状態と再生中の音声を分け、戦闘へ移った後の反復観測で警告本文を止めません。川柳は危険中に保持して表示と時計をpauseします。共同編集の戦闘後の再開会話は後続段階です。

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

現在のworkshopは意味の相談、読み・音数・出典の確認、三行の表示と終了に対応します。検査はRustで行い、結果を見て説明する一手だけを追加できます。句の相談は実再生済みの直近4往復を独立して保持します。「終了」「終了でいいよ」はモデルを使わず即時に閉じます。自然な終了も原文の意思を検証します。

一行編集・修正案・採否・lesson・想起、意味説明後の納得からの終了確認、戦闘後の再開意思確認は後続段階です。編集や保存を実行した扱いにはしません。prompt・JSON契約と発話根拠の検査・読み補正は一時的なPython補助を残しています。

相談段階の検証:

```bash
python dogido-rust/scripts/compare_workshop_inspection.py
python -m pytest dogido-rust/scripts/test_workshop_helper.py -q
python dogido-rust/scripts/check_workshop_runtime.py
```

実モデルの相談だけを試す場合は、起動済みの接続先を明示します。観測・句・音声は模擬で、実ゲームやスピーカーは使いません。このコマンド自体はMLXを起動・停止しません。

```bash
python dogido-rust/scripts/check_workshop_live.py --base-url http://127.0.0.1:8080/v1 --output .dogido_tmp/workshop-live.json
```

```sh
python dogido-rust/scripts/test_haiku_preparation.py
python dogido-rust/scripts/compare_haiku_record.py
python dogido-rust/scripts/check_haiku_runtime.py
```

最後の検証は空きローカルポートの模擬モデル・TTSと無音playerを使い、終了時に所有プロセスを回収します。実モデルの品質・速度とMinecraft・音声実機の確認は別途必要です。

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
- LLM境界、session／heartbeat／player-inputの外形、ゲームイベントの受信モデル、通常会話plannerの型を移植しています。句の自動保存形式もPythonと照合済みです。設定全体やrevision・lesson等の記憶形式は、後続の移植時に検証します。
