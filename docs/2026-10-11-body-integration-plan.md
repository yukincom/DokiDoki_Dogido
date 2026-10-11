# 3D身体の本線統合と観測地点の移行

状態: 2026-10-11、専用ワークツリーの準備と身体0.3.0のGit管理。接続実装は未着手。

## Gitの基準と作業範囲

- ブランチ: `codex/body-integration`。
- 分岐元: `63459cf5a23ee66ad79ec0cade0cc3a376fc30bc`（mainの確定済みコミット）。
- 書類フォルダの本線と並ぶ `DokiDoki-Dogido-body-integration` を分離作業に使い、統合先と通常の起動場所は `DokiDoki-Dogido` のmainとする。
- 本線の未コミット変更はこの基準へ含めない。ユーザーから本線テスト解消の報告あり。テスト修正や管理アプリ等の後続コミットが確定したら、接続実装の前に取り込む。古い検査記録の失敗を現在の残件として扱わない。
- 既存の `animation/cube_dogido/cube-dogido-lab` からソース・テスト・ビルド定義・wrapper・モデル元データ48ファイルをハッシュ照合して取り込む。元ファイルの場所は保つ。
- 身体プロジェクトだけGit除外を解除する。build、Gradleキャッシュ、ゲームのrun、復旧アーカイブは除外し、既存の設定・記憶・ログを取り込まない。
- 変更単位ごとに差分と関連検査を確認してローカルコミットする。実装が揃ったら全体検査を行い、mainへ統合する。
- mainへの統合前に、Git除外中だった本線の身体フォルダと本ブランチを比較する。元データ等の未取込編集を保存してから合流し、Gitによる上書きへ任せない。

## 起動の入口

本線統合後も現在の入口を使う。身体はMinecraft内のModとして動き、HTTP・モデル・TTSは既存のRust本体とFabricアダプターが担当する。身体専用の常駐サーバーや別ポートは追加しない。

```sh
./dogido-rust/start_dialogue.command
# マイクを使う場合は別ターミナル
./dogido-rust/start_voice.command
```

一般の `python dogido-rust/scripts/launch_dialogue.py --settings-dir .` と `--voice`、`scripts/start_dogido.command server|voice` も既存の入口として維持する。詳細は[Rust本体の案内](../dogido-rust/README.md)。

worktree内の `start_dialogue.command` / `start_voice.command` は、そのworktreeのRust実行ファイルを使い、既定の設定とPython環境はGit共通ディレクトリ側から解決する。起動場所を変えると実行対象も変わる。通常運用は本線の既存入口を使う。開発版の実機試験では、その版のビルド・試験用記憶・接続先を明示して本線と取り違えないようにする。

設定・記憶を新規worktreeへ自動コピーしない。今回の準備では起動スクリプトの変更・プロセスの起動停止・ゲームのMod交換を行わない。統合版の実機試験では本体と対応するModのビルド・導入確認が必要になる。

## 観測と行動の担当

| 対象 | 観測基準・担当 |
|---|---|
| 平常時の視覚・聴覚・嗅覚、会話・川柳の周辺材料 | ドギドの身体で実測 |
| 戦闘開始・被弾・プレイヤー周辺の危険 | プレイヤー側で継続監視 |
| プレイヤーのHP・持ち物・操作・指差し | プレイヤーの事実として保持 |
| 身体の移動・休息・食事の選択 | Rust側の身体用モデル処理 |
| 移動の物理処理、損耗・回復・食料消費・復活 | 身体Mod |

身体の観測が欠けた場合は理由付きの未取得として扱う。プレイヤーの観測値を身体の観測へ代入しない。HTTP session、adapter worldEpoch、Body Contextは別々に照合し、実行直前にも確認する。身体側のHPや敵の接近だけでプレイヤーの戦闘状態を立てない。

## ファイル分割案

以下は実装先の案であり、未作成のファイルを含む。既存の入口・分類・スメル規則を先に調べ、意味の重複があれば新設前に報告する。共有ライブラリの新設と配布方法は、実際に共用する処理を確定してから決める。

| 配置 | 担当ファイル案 | 状態の所有者 |
|---|---|---|
| 身体Mod `dogido/lab/observation/` | `ObservationOrigin`、`BodyObservationCollector`、`BodyVisionSensor`、`BodyEnvironmentSensor`、`BodySmellSensor`、`BodySoundSensor` | 身体ごとの感覚と観測履歴 |
| adapter `dogido/fabric/body/` | `BodyConnection`、`BodySnapshotReader`、`BodyObservationCodec`、`BodyCommandExecutor`、`BodyResultTracker` | 接続世代、結果の配送と再送 |
| Rust `src/body/` | `mod.rs`、`contracts.rs`、`observation.rs`、`state.rs`、`planner.rs`、`validation.rs`、`results.rs` | session内の身体観測と操作台帳 |
| Rust `src/dialogue/body_runtime.rs` | 会話処理と身体用処理の接続 | ライフサイクルの配線 |

`DogidoEntity`、adapterのroot、`dialogue/mod.rs` には担当への呼出しを置く。1ファイル100〜250行を目安にし、350行を超えたら責務を見直す。数値は機械的な分割条件にせず、変更理由ごとに追えるまとまりを保つ。

ファイル先頭に役割・入力・出力・状態の更新担当を日本語で書く。各処理ブロックにも目的、必要な理由、値の単位、未取得時の扱い、復活・次元変更時のリセット条件を記す。観測の採取、形式変換、行動選択、実行、結果の反映をコメントで区切る。

## 最新本線との接続位置

- 送信は `adapter/minecraft-fabric/src/main/java/dogido/fabric/protocol/DogidoEventClient.java`、既存操作の解釈は同ディレクトリの `DogidoCommandProtocol.java`。HTTPの往復を再利用し、身体の型・capability・結果を追加する。
- 観測は既存の `observation/` に分割済み。匂いは `smell_policy_v2` を基準にし、方向・座標・距離・個体IDを匂いへ混入させない。
- 現行の通常会話は一回のモデル呼出し。旧read plannerを復活させず、身体の行動選択を専用の責務として接続する。
- 発話履歴は音声完了または対応する文字表示ACKから一度だけ確定する。Chrome検索開始の実音声完了条件は別に保つ。
- 音はプレイヤー側で受け取ったイベントだけで身体の聴覚が成立するかを先に確認する。持ち物由来の匂いも身体との位置関係を整理する。

## 実装と検証の順序

1. 本線の後続コミットを取り込み、基準を更新する。
2. 身体の有無・観測地点・世代を明示する契約と、未取得の往復を接続する。
3. モブ・ブロック・環境を身体基準へ移し、playerとbodyが離れたfixtureで混同がないことを確認する。
4. 匂い・音・周期比較の履歴を身体単位へ移す。復活・次元変更・未ロードを検査する。
5. 身体のモデル選択と操作結果を往復させ、再送・重複・古い世代の結果を検査する。新規プロンプトの試験はユーザーのレビュー後に行う。
6. 全体自動検査の後、起動・Minecraft・会話・戦闘・川柳を実機で確認してmainへ統合する。

2026-10-06の身体単体記録は39テスト成功。本線接続、モデルの行動選択、実機確認とは区別する。今回の取り込み後の検証結果は下記へ追記する。

### 取り込み後の検証（2026-10-11）

- 新しいworktreeで `./gradlew --offline test build` が成功。39テスト、失敗・エラー・skipは0件。
- 元の48ファイルは変更なし。取り込んだ実行ソース・テスト・ビルド入力はハッシュ一致を確認した。追跡開始のため `.gitignore` と引き継ぎ文書を更新し、参照用エクスポートJavaの末尾空白2か所だけを整えた。
- 本線と起動スクリプト4本の内容が一致。新しい文書リンクと、成果物・キャッシュ・他の制作物のGit除外を確認。
- Rust本体・adapterの再検査、モデル接続、Minecraft起動、Mod交換は今回の準備に含めていない。
