# ドギド Rust版

ドギド本体を段階的に移植するための実装です。接続専用HTTPサーバー、RigのLLM接続試験、Python版との通信比較が動きます。

セッション登録・再接続・終了、表示データ、ゲームイベントの受信検査に加え、通常会話の限定plannerを単独実装しています。ゲームの判断、返答本文、短期履歴の更新、音声、HTTPから会話への配線は未移植です。進行は[移行計画](../docs/rust-migration-plan.md)を参照してください。

## ビルド

Rustは[rustup](https://rust-lang.org/tools/install/)で用意してください。`rust-toolchain.toml`でRust 1.98.1、`Cargo.lock`で依存を固定しています。Rigは`rig-core 0.42.0`です。

リポジトリのルートから実行します。

```sh
./dogido-rust/cargo.sh build --release --locked
```

`cargo.sh`は通常のRust環境のほか、`DOGIDO_RUST_TOOLCHAIN_DIR`で指定した場所の`cargo/`・`rustup/`を使えます。

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

## 通常会話plannerの単独確認

```sh
dogido-rust/target/release/dogido-rust plan-chat dogido-rust/fixtures/planner/explicit_repair.json
```

本人の訂正、聞き返し、会話継続、現在観測への照合など一件のread actionを選びます。現在の発話と実再生済み履歴に根拠があるかをRustで検査し、AIに履歴・世界・保存の変更権限を渡しません。引用内の訂正語や原文にない置換語は採用しません。対象候補と現在観測の照合、および未観測時の固定返答も独立関数として移植しています。

入力は状態機械が作る読み取り用の`PreparedPlan`です。現段階では現行Pythonが作った文脈・カタログ由来のfallbackを合成fixtureに書き出して使います。文脈の収集・カタログ検索・通常返答の生成はまだ移植していません。`serve`の会話APIには未接続で、このCLIだけでは相棒と会話できません。

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
- 生成本文が不完全なJSONでも、`finish_reason=length`と本文・トークン数を上位へ返します。内容の検査・採否・再生成はドギドの処理が担当します。通常会話plannerの契約検査は移植済みで、川柳などは未移植です。
- 対象は現在のMLXの通常のChat Completions応答です。Rigは`id`・`model`・messageの`role`等を要求するため、旧Pythonが許容する省略形すべての互換実装ではありません。
- LLM境界、session／heartbeat／player-inputの外形、ゲームイベントの受信モデル、通常会話plannerの型を移植しています。設定全体、記憶の保存形式の互換性は、それぞれの移植時に検証します。
