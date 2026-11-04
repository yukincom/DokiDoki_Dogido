# AGENTS.md — AI コーディングエージェント向け

現行の作業入口と、変更時に守る境界をまとめる。**2026-11-05に書類側mainの実装・仕様書と照合**。日付付き文書の計画・実装結果・実機結果は分けて読む。このファイルを過去の作業ログや次の依頼の代わりに使わない。

## 1. 最初に確認すること

1. 開発の本体はユーザーの書類フォルダ内の `DokiDoki-Dogido`、ブランチは `main`。作業場所・現在のHEAD・`git status --short` を確認する。Codex内部のworktreeを継続開発の本体にせず、分離作業も書類側mainへ統合し、案内するファイルと起動場所を揃える。
2. [README](README.md)で構成と起動方法を把握し、下の責務表から変更対象を探す。[文書案内](docs/README.md)と対象分野の現行仕様・実装・テストを照合する。
3. 11月2日の川柳材料・モブ質問・一行編集と視覚入力のA〜Fは、**11月3日に本線へ統合済み**。[統合後の確認手順](dev_tools/実機確認_20261102/手順.md)を参照。古い仕様書冒頭の「次は」「専用作業ツリー」「統合待ち」や番号付き復元コマンドを現在の作業指示にしない。
4. 実装済み・自動確認済み・実モデル／Minecraft／音声で確認済みを区別する。過去の成功件数や他ブランチでの確認を、現在の変更の合格根拠へ読み替えない。文書と実装が食い違えば差を報告し、古い方式を復活させて帳尻を合わせない。

### 作業の進め方

- ユーザーの依頼範囲・優先順位・締切を守る。必要な確認が済んだら完了報告し、無関係な修正・リファクタリング・テスト期待値更新・文書整理へ広げない。発見した別件は報告に分ける。
- 既存のユーザー変更を保護する。無断のstash・reset・restore・一括stageはしない。大きな変更前に復旧できるHEADを記録し、現在の依頼に属する差分だけをレビュー・検証した安全な区切りでローカルcheckpointを作れる。コミットは日本語。push・公開・履歴書換えは別途の明示依頼に従う。
- 並行作業は範囲を分け、編集する担当ごとにworktreeを分離する。親担当が統合と最終確認を行う。実機確認を統合条件として指定された変更は、その条件を守る。
- **モデル向けプロンプトを変える前に「元プロンプト → 新プロンプト」を本人へ示し、了承後に反映・試験する。** JSON正本だけでなくRust内の指示文・tool説明・観測文面も対象。文書更新とモデル入力の変更は区別する。
- 本体・モデル・マイク・Mod・配布アプリの起動、再起動、差し替えは、その作業の依頼範囲を確認して行う。ビルド成功を配備済み・実機確認済みとは報告しない。
- コメントは日本語で、担当機能・入力と出力・状態更新の所有者・単位・省略時の意味を説明する。CASは元句と版の取り違え防止、epochは古い処理結果の混入防止など、条件の理由を近くに残す。
- 秘密情報・認証値・私的な画像や採取データをコミットしない。READMEや共有文書に特定マシンのホーム絶対パスを埋め込まない。

## 2. 構成と責務

Minecraftの状況を受け取り、怖がり相棒ドギドが警告・雑談・川柳を返すリアルタイム相棒サーバー。

```text
Fabric（身体・観測・限定操作） → Rust（状態・配送・保存） → ローカルLLM / TTS
```

判断・優先制御・取消・世界操作・保存の整合性はRustとFabric、意図分類はBERT、自然な返答・意味説明・句案・川柳相談の語句や採否の解釈はLLMが担当する。既知の実行データを別のLLMへ再質問しない。汎用エージェント基盤や旧Python本体へ置き換えない。

| パス | 役割 |
|---|---|
| `dogido-rust/src/dialogue/` | 入力・セッション・会話・音声・記憶の配線。取消と状態適用の所有者。`mod.rs`へドメイン処理を集積しない |
| `dogido-rust/src/request_classifier.rs` · `dogido-rust/src/dialogue/request_runtime.rs` | 川柳／通常のBERT受付、入力世代とモードの固定 |
| `dogido-rust/src/combat/` · `dogido-rust/src/environment/` · `dogido-rust/src/foreground.rs` | 戦闘・環境・発話優先、会話の所有権 |
| `dogido-rust/src/chat_materials/` · `dogido-rust/src/chat_observation.rs` · `dogido-rust/src/conversation_observation.rs` | 現在観測・直前の視認・音・対話由来の材料。`dogido-rust/src/catalog_knowledge.rs`は一般知識 |
| `dogido-rust/src/chat_validation.rs` · `dogido-rust/src/dialogue/history.rs` | 通常会話の形式補完、配送後の短期履歴 |
| `dogido-rust/src/body/` · `dogido-rust/src/dialogue/visual_runtime.rs` · `dogido-rust/src/llm/image.rs` | 身体の依頼・観測、画像の時系列入力 |
| `dogido-rust/src/haiku/` | 自動発句の材料選択・前置き・生成・形式検査・再生成 |
| `dogido-rust/src/workshop_prompt/` · `dogido-rust/src/workshop_projection/` · `dogido-rust/src/workshop_response/` | 川柳相談の入力、モデル出力の受信・補完 |
| `dogido-rust/src/workshop_editing/` · `dogido-rust/src/workshop_edit.rs` · `dogido-rust/src/dialogue/workshop_edits.rs` | 差分・未採用案、元句と版の照合、採用・保存・表示 |
| `dogido-rust/src/dialogue/workshop_runtime.rs` · `dogido-rust/src/dialogue/workshop_combat_runtime.rs` | 相談の配線、句と返答を保持した戦闘中断・再開 |
| `dogido-rust/src/haiku_record/verse.rs` · `dogido-rust/src/haiku_memory.rs` · `dogido-rust/src/dialogue/memory_runtime.rs` | 行の表記・読み・材料・来歴、句・revision・critique・soft lesson |
| `dogido-rust/src/haiku_artifacts.rs` · `dogido-rust/src/episode_log.rs` | 発句素材の帰属と保存／整理、会話記憶へ読み戻さない評価記録 |
| `dogido-rust/src/assist/` · `dogido-rust/src/knowledge/` | 明示依頼の限定操作、詩の基礎知識・Minecraft正本DBの照会 |
| `dogido-rust/src/player_text.rs` · `dogido-rust/src/contextual_asr.rs` · `dogido-rust/src/voice/` · `dogido-rust/src/tts_reading.rs` | 原文を残す入力正規化、現在候補のSTT補正、録音・認識・読み・配送 |
| `dogido-rust/scripts/` · `dogido_server/` | 起動・設定、UniDic token、Whisper、AEC、BERT等のPython補助。会話判断や保存判断を戻さない |
| `dogido_server/llm/companion_prompts.json` · `dogido_server/llm/reaction_situations.json` | 共有人格・対話目的・状況文の正本。Rustビルドへ反映 |
| `adapter/minecraft-fabric/` | tick・通信・観測・身体・限定操作・HUD。配置とビルドは[adapter README](adapter/minecraft-fabric/README.md) |
| `dogido-rust/tests/` · `tests/` · `dev_tools/` | Rust／Python検証と独立試作。試作の存在を本体接続済みと扱わない |

Python補助の存続範囲は[Rust README](dogido-rust/README.md#残すpython補助と資料)を参照。読み判定はRust、UniDic取得は `dogido-rust/scripts/tts_shared_tokens.py` の共有 `Unidic()`、データ整備補助は `dev_tools/catalog_tools/`。旧Python本体・比較oracleを再導入してfixtureを作り直さない。

## 3. 入力・会話・音声

### BERTの受付

- 共通受付 `request_runtime::Binding` でモードを固定し、一入力を一度だけ分類する。川柳相談中は8分類、それ以外・戦闘中断中は通常16分類。掛け軸の非表示だけではモードを変えない。掛け軸・STT文脈も同じ受付状態を使う。
- 採用モデルは川柳が `models/dogido-senryu424-seed42`、通常が `models/dogido-normal16-seed48-mob`。設定は `request_classifier_senryu_model_root` / `request_classifier_normal_model_root`。導入は[同梱分類器の設定](docs/2026-10-24-bundled-request-classifier.md)に従い、Git LFS実体と設定を確認する。共有JSONのパス既定はnullで、未設定側を自動選択しない。両モデルの推論は共有Mutexで合計1件に制限する。
- 失敗・対象外・片側未設定でも他方・旧23分類・OS AI・旧LLM分類へfallbackしない。入力元・接続・句ID／版・中断境界を照合し、遅延結果を新モードへ付け替えない。
- 通常のモブ情報ラベル `EnemyInformation` は `player_chat` へ渡す。モブの有無・数・方向の質問を旧戦闘定型文へ戻さず、危険警告のコード経路とは分ける。
- `dev_tools/edit_span_extractor/` の語句抽出は独立試作で、本体の一行編集へは未接続。分類・抽出の拡張は担当の進捗と正本教材を確認し、ラベル案・教材・学習済み・本体採用を区別する。

詳細：[BERT分離](docs/2026-10-24-bert-mode-routing.md)、[モブ質問と一行編集](docs/2026-11-02-senryu-materials-mob-chat-edit-input.md)。

### 通常会話と配送

- `player_chat` は今回入力・現在観測・確定済み短期履歴から一回で話題と返答を生成する。前段planner、生成後の意味・観測整合の再審査、形式修復の再生成を追加しない。通常会話・環境反応には関連する材料だけを渡し、辞書描写を重ねない。
- JSON末尾や単純な型違いはコードで補完する。空・文字化け・明らかな反復・補完不能な外形だけを不成立とし、日本語・方言・名前・嗅覚・内容の解釈を固定語句で拒否しない。
- 通常会話と環境反応は `speak / silent` を使う。沈黙は発話のない出来事で、再生済み発話や川柳材料に数えない。通常会話の不成立・通信失敗も、先行再生前だけ「難しい話だったので、ドギドは沈黙を選んだ」の出来事へ接続する。先行再生後の失敗を沈黙へ読み替えない。
- 新しい文字・認識入力は前の音声完了を待たず共通受付へ進める。次の生成と音声配送の枠を分け、音声は順番に再生する。通常の観測変化・失効・期限だけで再生を止めず、緊急警告・明示停止・voice off・接続／世界／サーバー終了の取消は維持する。
- 川柳の生成・前置き・本文・相談中、および通常会話の再生中の夕方警告は、既存の抑止・保留を維持する。緊急警告や戦闘の優先制御とは分ける。
- 音声ありの通常会話とWSの会話返答は、actionと本文の文境界が確定した順に読み補正・合成・再生する。JSONキー・reasoning・未完成のtool引数は読まない。操作結果、音声なし、自動発句、環境反応はそれぞれの完了後処理を使う。
- assistant履歴は全文の表示ACKまたは全音声の成功で一度だけ確定する。`text_displayed` と `playback_status` は別。失敗・取消・古い世代を発話済みにせず、確認済みの全文表示履歴は後続の音声失敗でも失わない。固定警告・支援・前置きも配送済みなら共有し、句本文・相談・記憶操作は出来事として扱い川柳材料へ重複登録しない。
- `voice off` はマイク・STT・TTSを休止する。STT補正は `source=voice` と現在候補に限定し、原文・正規化文を保持する。typed入力や語句の会話を音声用の操作補正へ流さない。

詳細：[本体対話](docs/main-dialogue-integration.md)、[文字配送](docs/2026-10-09-minecraft-text-dialogue.md)、[通常会話の文ストリーム](docs/2026-10-26-chat-sentence-stream.md)、[WSの文ストリーム](docs/2026-11-02-workshop-sentence-stream.md)。

## 4. 川柳相談と自動発句を分ける

### 川柳相談（workshop / WS）

川柳は人間とAIの認知のぶれを集める窓口で、あとから句集でも編集できる。その場の句をコードの品質判定で完成形へ絞らない。

- 行指定・具体変更・終了はBERT、語句・新案・採否・称賛・lesson解除はLLMが解釈する。コードは現在句・対象行・元句と版・入力世代・対象外の二行・取消・保存を照合して実行する。
- 行指定だけなら相談対象を保持する。具体変更は専用入力で、現在三行・対象・今回依頼・直前の通常会話と相談履歴・該当する案を渡す。履歴本文を削って短縮せず、不要な操作説明・例文・出典を外す。
- 一回の呼出しで `stage_player_edit` の変更後の一行全体、または `ask` を受け取る。分類済みの行を選び直さず、原文の正規表現から直接実行したり、モデルの語句を再抽出して上書きしたりしない。同文なら保存しない。本人指定の表記を尊重し、未知の読みは未確定のまま保持する。
- 音数計測・5/7/5比較・固定の音数案内・品質評価・原文一致・confidence閾値・疑問や否定等の語形・hard禁止語・材料の欠落や重複を編集の拒否条件にしない。`evidence / confidence` は任意の記録。旧 `workshop_validation`・`workshop_input_guard` を復元しない。
- 読みや保存材料を本人が尋ね、手元に情報がないときだけ既存の参照toolで調べ、一度だけ返答を続ける。編集の前提検査や、編集失敗後の再応答生成へ戻さない。
- 新案は一案だけ生成して未採用案へ置く。本人による現在句の編集は保存後に反映し、未採用案の編集はpendingだけを更新する。採用時は同じ元句と版へ再照合して保存する。材料IDの一致を意味や実使用出典の検証済みと扱わない。
- BERT終了でpendingがなければ閉じる。pendingがあればLLMが採用・破棄・会話を選び、採用／破棄の成功後だけ閉じる。採否不明・askではopenとpendingを保持する。称賛だけで終了・lesson解除はしない。終了を伴わない採用後は相談を続ける。
- 戦闘では句・pendingと受付済み返答を保持する。敵の現在観測と警戒解除条件からコードで再開し、再開のAI分類や同意語判定を戻さない。同じworkerの生成・編集・保存を重複実行せず、再生済み文は繰り返さない。明示停止・voice off・死亡・次元変更・切断・終了は保留を終端する。
- WSの受付がopenの間は通常の周辺走査・定期画像を休み、体力・足場・交戦等の軽量観測とプレイヤーへの注視を使う。未測定項目を新材料にせず、明示撮影・句集用撮影は別扱い。終了・戦闘移行時には通常観測へ戻す。

詳細：[受信と適用](docs/rust-workshop-validation.md)、[一行全体の編集](docs/2026-10-30-workshop-complete-line.md)、[戦闘保留](docs/2026-10-28-workshop-reply-resume.md)、[WS中の観測](docs/2026-11-03-workshop-observations.md)。全文直し・自作句保存は `poem_input.rs`、読み辞書の編集はあんちょこ画面が入口。

### 自動発句と記憶

- 通常周期は10分。通常返答の後ろ／安全なqueue境界で開始し、`knowledge`中は時計を凍結する。試験用の短縮設定を通常運用へ持ち越さない。
- 景色・天候・モブ、直近の視認、配送済み会話を出典と時点を保って材料へ渡す。持ち物は発句時の空でないhotbarだけ。欠測・全枠空でも全インベントリへ拡張しない。落下物は周辺観測として別に扱う。
- `haiku_irony` と `haiku_scene` は同じ元材料候補を使い、前置きで選んだ材料を三行生成・再生成へ引き継ぐ。前置きで使った材料が1〜3件なら全件、それ以上なら選択された部分集合。未選択の全観測を後段から足さない。
- 自動発句には既存の音数・文字種・道具／読みのhard制約と再生成上限がある。空行・折返しを補完し、意味・自然さの評価LLMや材料固定語リストによる棄却は行わない。この検査をWS編集へ流用しない。
- `line_sources` は提示資料の記録で、意味的な実使用の証明ではない。句は基本auto-save、revision・critique・lessonは既存記憶処理で保持する。過去revisionを常時few-shotへ積まず、想起は明示クエリで行う。
- player lessonはsoft参考。`forbidden_fragments`をhard禁止に合流しない。称賛はcritiqueだけでlessonを消さず、解除は `polarity=loosen`・`lesson_type="*"`。WS内はLLMの `clear_lessons` を既存保存へ渡す。日数・発句回数のTTLを維持し、strengthを新たな強制条件にしない。

詳細：[前置きと材料](docs/2026-10-29-haiku-preface-materials.md)、[11月2日の材料仕様](docs/2026-11-02-senryu-materials-mob-chat-edit-input.md)、[生成・知識範囲](docs/2026-10-21-generation-and-knowledge-scope.md)。

## 5. 身体・観測・限定操作

- ドギドはMinecraft内の相棒で、実ロボットの静止・整定を撮影の前提にしない。移動と向き変更を並行し、生成・発話中も身体移動を続ける。位置差・角度差・停止状態を明示撮影の採否に使わず、撮影後の姿勢で画像を破棄しない。特定モデルのぶれ補正能力を断定しない。
- 自分の身体の地点・向きに基づく観測を使い、プレイヤーの視界・所持品・報告と混同しない。画像は比較が成立した変化時だけ比較元→今回の2枚、それ以外は今回1枚。身体の方位変化と実在するPLAYER枠の説明を添え、ハッシュ・マス・処理番号をモデル説明へ出さない。
- 周囲の視認一覧と身体を向ける注目候補は別。注目対象だけを「見えているもの」の一覧にしない。現在視認・直前の視認・音を分け、辞書の一般知識やプレイヤーの報告を自分の目撃へ昇格させない。
- 通常会話・環境反応のモブ提示は「いま見えとるもん」と「さっきまで見えとったもん」に分ける。後者は既存の直前の視認変化から、現在見えていない種類だけを取り出す。不完全観測や世界変更から消失を推定しない。現在見える音源の実音は会話材料へ重複して載せず、自発反応では同じ音源・音種の継続を新着扱いしない。戦闘用の聴覚は別に維持する。
- 敵の視認は、同じ個体の実視認が続く間だけ既存の外周まで保持する。喪失した個体を死亡・撃破へ読み替えず、音だけから視認・召喚成功・プレイヤー撃破を確定しない。友好・資源モブへの操作禁止を語らず、中立モブには優しく接する方針を使う（[モブ反応のトーン](docs/mob-interaction-tone.md)）。
- 匂いはFabricの `smell_policy_v2`（`none / present / suppressed` とcomponents）が正本。同種非加算・異種の抑制を解決した各成分と強さを使い、方向・距離・個数・entity IDを足さない。匂い単独で戦闘／WS中断を起こさない。雨上がり候補は通常臭と別で、反応完了／沈黙確定後に通常へ戻す。
- 空・地下・降水は既存の投影条件を守る。乗車は乗車中だけ主語付き事実で渡す。照明のinventory増加をクラフト／設置と断定せず、既存の抑制を通った場合だけ反応選択と本文を一回で生成する。環境の候補・間隔・優先・取消はコード、話す／黙ると本文は既存会話モデル。
- `select_sword` は明示依頼だけで、自動持ち替えはしない。乗れる動物14種の呼び戻しは依頼後の `list_horses / select_horse` の二段階。所有者・装備・現在候補・世代・期限・重複をRust、足場・水域・身体空間と実移動を統合サーバーで再検査する。汎用の世界操作toolへ拡張しない。

詳細：[身体と撮影](docs/2026-10-26-look-capture-and-darkness.md)、[身体観測と会話](docs/2026-11-03-body-observation-dialogue.md)、[時系列画像](docs/2026-11-02-vision-frames.md)、[視認と注目](docs/2026-11-04-vision-and-attention.md)、[モブ提示](docs/2026-11-04-visible-mob-presentation.md)、[敵の外周追跡](docs/2026-11-04-hostile-vision-margin.md)、[匂い](docs/smell-policy.md)、[動物呼び戻し](docs/2026-10-11-horse-recall.md)。

## 6. 保存・設定・起動

- 常用の生成系はllama.cppの `llama-server` とGemma 4 26B-A4BのGGUF。通常会話・画像・川柳は `default_model` とサーバーのプロンプトキャッシュを使う。保存場所・起動プロファイル・確認方法は[共有LLM](docs/shared-llm-profile.md)を参照し、実際の起動状態はその都度確認する。
- 共有の動作既定値は `dogido_server/runtime_defaults.json`、一般設定・ポート・保存先の解決は `dogido_server/config.py` と起動処理を確認する。Python型・制約とRust検証を既定値の重複と誤認しない。設定を撤去する前に本体・`--voice`・配布の消費先まで追う。`voice_vad_cli` は音声起動JSONからRust STTへ渡る現役設定。
- 本体・本棚・相談室は同じ `memory_dir` を使う。SSDを正本にする環境では `DOGIDO_MEMORY_DIR` を揃え、旧ローカル保存先へ混在させない。既存記憶は設定で引き継ぎ、起動時に自動移動・統合しない。[本棚の保存構成](docs/2026-10-13-bookshelf-integration.md)を参照。
- SSD正本環境の研究履歴は原句日時で `YYYY/YYMMDDHHmm`、旅は `journeys/YYMMDDHHmm/<旅ID>.json`（日本時間）。句の編集・想起は同じ保存先の `sessions/`・`long_term/` のJSONLを使う。研究履歴の配置と句の正本を混同しない。
- 画像・内部表現は対応する正本と照合して保全・整理する。`artifact-jobs` の帰属・保存状態とwriter終了・参照・内容照合を使い、取消／不成立の専有素材だけを整理する。保存待ち・保存失敗・処理中・帰属や参照が不明の素材を古さや句の不在だけで削除しない。[素材のライフサイクルと確認結果](docs/2026-11-04-haiku-artifact-lifecycle-plan.md)を参照。
- `eval/episodes.jsonl` は発話なしも含む決定記録で、句の記憶や会話へ読み戻さない。`service_decision` は実再生成功ではない。記録失敗をリアルタイム処理へ伝播させない。
- 通常のローカル起動は `apps/ドギド サーバー.app`、CLIは下記。家庭内LANは[遊ぶMacの導入](scripts/lan/遊ぶMacの導入.md)と[LAN運用](docs/2026-11-03-remote-mac-runtime-and-multiplayer-plan.md)を使う。Bonjour・初回双方承認・HTTPSで共有LLMへ接続し、本体・音声・記憶は遊ぶMacごとに分ける。本棚・研究資料の共有計画を実装済みと扱わず、旧SSH起動計画や統合前のMod復元コマンドへ戻さない。

リポジトリルートで、準備済みのPython補助環境を使う。`--check`はサーバー・生成・録音を始めない事前確認。

```sh
python dogido-rust/scripts/launch_dialogue.py --settings-dir . --check
# 本体を起動する依頼のとき
python dogido-rust/scripts/launch_dialogue.py --settings-dir .
# 音声を使うときは別ターミナル
python dogido-rust/scripts/launch_dialogue.py --settings-dir . --voice
```

`scripts/start_dogido.command server|voice` と `dogido-rust/start_dialogue.command` / `start_voice.command` も一般設定のポート（既定5055）と保存先を使う。相談室の独立HTTPは既定5057。準備・依存関係・その他の入口は[Rust README](dogido-rust/README.md)、テキスト注入は[adapter API](docs/adapter-api.md#21-post-apiv1player-input)を参照する。

## 7. 変更の確認

- Pythonテスト・模擬worker・fixtureは `dogido-rust/tests/python/`、共通Pythonテストは `tests/`。本体起動用 `scripts/` へ混ぜない。
- **自動テスト・模擬接続は試験ごとの一時保存先へ隔離する。** 旅・句・短期記録・本棚・画像・内部表現・評価ログを含め、本番SSDの設定を継承しない。Python HTTP試験は `storage_fixture.isolated_storage` を使い、所有する一時領域を終了後に整理する。隔離できない試験は起動しない。
- 通常試験は模擬モデル・採取なし。実採取を試す場合はモデル側も専用の一時採取先へ向ける。`check_*.py`がすべて模擬とは限らないため、ファイル冒頭・引数・fixtureを読んで選ぶ。
- 検証は変更の影響範囲に合わせる。テストを新設・改訂するときは期待動作・観測できる合格条件・検証範囲を明記し、接続確認と実モデル／実機の品質確認を分ける。無関係な既存失敗は別件として報告する。
- 該当確認が通ったら、`git diff --check`、完全な未stage差分とstage済み差分、`git status --short`を確認して完了報告する。別の懸念がなければ全体試験の反復や追加整備を続けない。

必要に応じてリポジトリルートから実行する。文書のみの変更ではリンク先・記述の根拠・差分を確認し、実モデルやサービスを起動する必要はない。

```sh
./dogido-rust/cargo.sh test --all-targets --locked
./dogido-rust/cargo.sh clippy --all-targets --locked -- -D warnings
./dogido-rust/cargo.sh build --release --locked
python -m pytest -q tests dogido-rust/tests/python/test_*.py
```

模擬HTTP試験は[Rust Pythonテスト案内](dogido-rust/tests/python/README.md)、Fabricは[adapter README](adapter/minecraft-fabric/README.md)、実機は[本体の実機チェック](dogido-rust/manual-dialogue-check.md)と[統合版の確認項目](dev_tools/実機確認_20261102/手順.md)を参照する。

## 8. 文書の読み方と保守

- コンセプトは[README](README.md)・[concept](docs/concept.md)、完成度の考え方は[companion-maturity](docs/companion-maturity.md)。後者の昔の段階表を現行の実装チェックリストにしない。
- 旧23分類・旧learn／国語学習・Web対話・通常会話planner・WS音数検査・評価LLMの履歴は再実装の根拠にしない。知識回答の現行範囲は詩の基礎資料とMinecraft正本DB。
- `plan`という名前だけで未実装と判断しない。本文末尾の実装・検証結果と、実際の呼出箇所・設定・consumerを確認する。反対に試作・教材・モデル重みの存在だけで本体採用と判断しない。
- 挙動を変えたら対象の現行仕様と検証範囲を更新する。AGENTS.mdには導入時に必要な境界と参照先を残し、日々の試験件数・一時的な担当状況・「次にやること」を追記し続けない。過去の詳細は対象文書やGit履歴で追う。
