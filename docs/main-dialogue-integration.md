# 本体の会話所有権・中断・再生確定

**現行の所有者: Rust本体（2026-10-03）。** この文書の会話所有権・原文根拠・実再生完了・同意の契約は継続します。旧Python本体と独立試験ホストの運用は終了しました。現行実装は `dogido-rust/src/dialogue/`・`src/foreground.rs`・`src/language/`、起動は [Rust本体](../dogido-rust/README.md) を参照してください。以下の日付付き検証記録は当時の結果で、移植後の実機確認済みへ読み替えません。

**状態:** 2026-09-14、限定国語対話・同意済みの専用Chrome検索・話題転換・本人barge-in・危険前履歴保護の変更をmainへ統合。コード・自動テスト済み。ユーザー環境ではMinecraftの自動ポーズと川柳生成カウント停止を確認済み。統合後の実Minecraft、実モデル、実TTS、実Chromeを重ねた本体E2Eは別途確認する。

**2026-09-17更新:** 情景音声と本句生成をworkshop外の準備段階へ分離。全Pythonテストと、実モデルを使う独立service試験で、情景を先に出力し本句完成後にpinを開始することを確認。実スピーカー／Minecraft画面の一連の確認は未実施。

独立試験で確かめた国語対話を本体へ接続した契約を定める。現在はRustの限定国語対話と本体sessionがこの境界を所有する。戦況、assist、川柳workshop、保存判断は従来どおりコード側が所有する。

## 1. 会話ルートの所有者

session内に、同時に一つだけforeground routeを置く。

| route | 役割 | 非敵対ambient | 新しい自動川柳 |
|---|---|---:|---:|
| `none` | プレイヤー主導の話題なし | 可 | 通常条件で可 |
| `casual` | Minecraft観測を使う既存 `player_chat` | 最後の入力から30秒抑止 | 10分周期で可 |
| `learning` | 正本DBの明示知識回答と、国語・語句の限定対話 | 抑止 | 周期ごと凍結 |
| `web` | Web閲覧中の一時対話 | 抑止 | 周期ごと凍結 |
| `haiku_preparation` | 情景の先行音声と本句生成。pin・workshop期限は未開始 | 抑止 | pendingで抑止 |
| `haiku_workshop` | 既存の一句共同編集 | 抑止 | 既存workshop規則で抑止 |

- routeは長期記憶へ保存しない。
- 通常会話の本文履歴は従来の5往復を維持する。
- 雑談後の友好・中立Mobコメントは `DOGIDO_CONVERSATION_AMBIENT_MUTE_MS`（既定30秒）で再開する。会話保持の5分期限は維持し、入力待ち・危険・同種Mobのクールダウンは別に検査する。
- 国語・語句の明示質問と `learning` 中の続きだけを有界な専用workerで生成し、ゲームイベントの直列workerをLLM待ちで塞がない。正本DBの即答は従来どおり状態機械が所有する。
- 世界操作・敵方向・所持品・明示知識DB・workshop入力は、このworkerへ渡さない。
- 入力受付では未完了jobの上限を検査し、満杯なら `input_queue_full` を返す。危険中の知識質問の保留枠は別に最大9件で、満杯なら `knowledge_queue_full`。通常会話へ誤配送したり、旧Pythonの共通入力FIFOへ戻したりしない。現行受付は [入力API](adapter-api.md#21-post-apiv1player-input) を参照。
- workerが一般雑談・Minecraft話題への切替を返した場合、独立試験用の仮想Minecraft文脈では本文を生成しない。元の `turn_id`・原文・sourceを保った型付き要求だけを本体へ返し、既存 `player_chat` が現在のゲーム観測で一度だけ答える。

### 学習中の突然の別話題

- 直前の会話活動から2分以上空いた入力、明示名指し、または明示的な話題転換は、新しい話題として直ちに本体 `player_chat` へ渡す。
- 2分未満で、学習内容と無関係かつ宛先も不明な突然の別話題は、その場で返事を生成せず、原文と元 `turn_id` を一件だけ `awaiting_address` に保つ。
- 後から「ドギド」等で呼び直されたら、コード固定の驚き・謝罪と、保留原文から切り出した短い話題を読み上げ、「今から聞いてええ？」と確認する。この修復質問の実再生 `completed` 前の肯定では元入力を動かさない。
- 肯定後は保留した元入力を同じ `turn_id` のまま本体へ一度だけ戻す。5分で `expired_unaddressed` として終端し、呼び直しでは期限を延長しない。
- 新しい宛先不明入力で一件枠を置き換える場合、古い入力も `replaced_unaddressed` として明示終端する。待ち列が満杯なら `host_chat_queue_full` とし、通常履歴へ混ぜない。

## 2. 再生完了を会話の正本にする

入力、生成した返答、音声再生を `turn_id` と `utterance_id` で結ぶ。

```text
player accepted
  -> reply selected
  -> audio queued
  -> audio started
  -> completed | failed | cancelled
```

- プレイヤー発話は受理時に台帳へ記録し、返答の所有routeが決まった時点で短期会話に共有する。状態機械が返す正本DB回答も同じturn IDを使う。
- ドギドの返答は、音声プロセスの `completed` を本体の配送結果として回収した後だけ履歴へ入る。
- `failed`、実際に `cancelled` となった音声、キュー置換、未配送の古いworker結果はassistant履歴や川柳材料へ入れない。
- workerの生成epochと、dispatcherへ渡した音声の事実を分ける。戦闘開始後でも、すでに再生開始して実際に正常終了した通常返答は `completed` のまま残す。実プロセスを止めた返答だけを `cancelled` にする。
- dispatcherが一つのbatchを取得した後に割り込まれても、現在actionと未開始の末尾actionすべてへ一度ずつterminal通知を返す。末尾を台帳上の `queued` のまま残さない。
- 本体の有効なplayer入力はbarge-inとして、現在の音声プロセスと未開始キューを止めてから、その入力を既存経路で一度だけ処理する。既知STTノイズ・空入力・満杯で拒否した入力では止めない。
- audio callbackはsession状態を直接変更せず、上限付きのイベント列へ結果を積むだけにする。
- 音声無効時は「選んだ本文」を「実際に話した本文」と見なさない。ゲーム外表示履歴とは別の境界である。

## 3. 戦闘による中断

hostileの視認・聴取、直近被弾、adapterのcombat activeを根拠に中断する。

1. `casual` または `learning` を一件だけ `suspended` に移す。
2. 実行中・完了待ちの学習結果をepochで失効させる。
3. 戦闘中に雑談を続けようとした最初の発話へ短く返し、30秒以内は同じ相槌を重ねない。
4. `combat_ended` では、戦闘中にも会話を試みた場合は通常の安堵文へ「戦うので覚えていない」旨を足す。元話題を保留したまま戦闘へ集中していた場合は、続きを促す別の短文にする。元話題がなければ一般会話の追記はしない。
5. 話題はgame tickや自動calloutでは減らさず、戦闘後に受理したplayer turnを10件数えた時だけ破棄する。
6. 「さっきの話」「話の続き」等の明示再開で元routeへ戻す。
7. 危険開始時の通常会話5往復を別枠で一時保護し、panic・警告・叫び声で押し出さない。危険終了後の新しい通常player turn 3件目の生成まで共有し、4件目から通常の直近5往復だけへ戻す。これは上記10 turnの話題bookmarkとは別状態である。
8. `player_died` は `combat_ended` を待たずforeground戦闘所有権を解放する。死亡そのものの発話は状態機械が所有し、一般会話の安堵文を重ねない。

音声認識結果が全文で反復叫声だけの場合、通常player input・workshop・学習worker・LLM会話履歴へ入れない。原文は上限付きの非永続診断だけに残し、LLMへは、同時点のコード観測から作った「敵対モブを視認」「爆発を観測」「原因不明の驚き」等の短い状況メモだけを渡す。叫び声の字面から敵・落下・爆発を推測しない。現行event契約には生存中の落下を確定する接地・落下距離がないため、落下は未観測時に断定しない。

川柳workshopの戦闘中断は `dogido-rust/src/workshop_combat.rs` と `src/dialogue/workshop_combat_runtime.rs` が所有し、この一般会話bookmarkへ置き換えない。

## 4. 雷と夕方

- 最初に実測した雷鳴／近距離落雷は現在のplayer replyより先に割り込み、同じ入力を次tickへ戻す。会話routeと履歴は消さない。
- 以後は既存のcue 10分・一言3分のcooldownを使い、再度話す場合は非割り込みの小さな懸念にする。
- 「player_chatを返したので雷を3分無視する」という旧規則は使わない。
- 地表夕方の既存警告も同じ入力を次tickへ戻す。洞窟・水中・安全な屋内の既存除外と、一晩一回の規則は維持する。

## 5. 雑談中の自動川柳

雑談が続いても発句を永久に止めない。

- 通常の10分周期が来ても、現在のマイク入力やplayer replyを途中で切らない。
- player replyがある境界ではその返答の後ろ、そうでなければ次の安全なstatus／音声キュー境界へ、生成した情景（ironyのdescription）を非割り込みの先行音声として置く。雑談中も固定の前置きへ置き換えない。音声本文はRustの発句準備と `dogido-rust/src/dialogue/haiku_runtime.rs` が組み立て、workshopやHUDの状態を変更しない。
- 再生完了済みの直近3 turnだけから、目標40〜60字・最大80字の短い会話材料、最大3語のmotif、元turn IDを作る。
- 会話材料は `player_reported_context` として元turn IDへ結ぶsoft材料であり、Minecraft世界の実測事実やhard制約へ昇格しない。
- 先行音声に含めた取り合わせだけを `spoken_preface` として出典へ結ぶ。これは川柳生成側の発話選択記録であり、スピーカー実再生完了の証明ではない。従来の未発話解釈 `generated_unspoken` は読み戻し互換として区別する。
- 情景音声から本句生成までは雑談を `suspended` に置き、foregroundを `haiku_preparation` にする。句が生成検査を通った時点でpinと `haiku_workshop` を開始し、その完了時刻から既存の無操作120秒／全体240秒を数える。生成前の観測時刻や古い待機入力へ時計を戻さない。失敗・危険による取消では新しいpinを作らず準備段階を終了する。
- 考え顔は実際の生成処理中だけ表示し、先行音声／句の配送前に通常顔へ戻す。掛け軸は完成した句だけを表示する。情景音声の生成・本句生成の所要時間はworkshopの持ち時間へ含めない。
- `learning` と `web` の間は発句間隔そのものを凍結する。解除直後に抑止時間分をまとめて経過扱いにしない。
- 発句準備中に危険が来た場合は、既存規則どおり古いprompt・材料・pendingを破棄する。

## 6. Web接続とMinecraftの自動ポーズ

### 本体の専用Chrome検索

本体 `MainLanguageRuntime` は、利用前提が揃うMacでだけ独立試験と同じ可視Chrome providerを受け取る。起動時の確認はファイル・設定・任意依存の読み取りだけで、ChromeやMCPプロセスは開始しない。

```text
国語・語句の問い
  -> Web検索の同意確認
  -> 「ほな一緒にいこか！」を音声キューへ
  -> 対応する発話IDの実再生 completed
  -> game-event worker外の有界workerで、専用Chromeへ検索を一度だけ開く
  -> foreground=web のまま、本人の報告・質問・明示復帰を待つ
```

- `DOGIDO_MAIN_LANGUAGE_WEB_ENABLED=true` が既定。専用MCP実行ファイル、MCP SDK、`show_browser=true` の表示用設定、Google Chromeのいずれかが欠ける場合はWebだけを無効にし、国語対話とsession作成は継続する。
- 同意前・案内音声の生成／開始だけ・`failed`・`cancelled`・古いepoch・開始前の戦闘中断・満杯で制御処理を受け付けられない場合は開かない。対応する `completed` は一度だけ消費する。既にページを開いて読書中なら、敵対警告を優先しつつ調査文脈は戦闘後まで保持する。
- 検索・15秒後の単発読み取りは既存の直列background workerで行い、ゲームイベント処理を待たせない。検索直後には説明音声を重ねず、表示されたページを読む時間を優先する。
- 調査文脈がある間は `foreground=web` が非敵対ambientを抑止し、自動川柳の時計を凍結する。通常会話の5分期限ではなく、既存のWeb読書用30分期限を使う。
- CAPTCHA、検索失敗、不正なproviderでは固定の失敗案内へ落として `learning` に戻す。通常のChromeプロフィールや既存タブは使わない。
- 「冒険に戻る」等の明示復帰で `foreground` を解除する。本体の短期文脈へ持ち帰るのは `return_context.researched_topic` の一件だけで、ページ本文・AI概要・URL・理解度は渡さない。復帰案内も他のassistant本文と同じく、実再生 `completed` 後だけ会話履歴へ入る。
- MCP接続と専用Chromeはsession runtimeが所有し、session終了時に閉じる。明示復帰時のMinecraft再フォーカス、専用タブの即時close、OS前面監視、自動再読はまだ行わない。

### Minecraft一時停止

2026-09-14、ユーザー環境ではMinecraftが自動ポーズし、ドギドの川柳生成カウントも停止していると確認された。現在の動作を採用し、一時停止・復帰の追加設計は残件から外す。従来の `client.game.pause.v1`／`pause_game`／pause ackの提案は採用せず、Chrome接続の前提にしない。この確認を別環境やmultiplayerでの動作保証へ拡張しない。

### 実機確認

未確認なのは次の範囲。

- 実Minecraftのevent列と実LLMの遅延を重ねた応答順
- 実TTSの完了callback、本人barge-in、危険割り込み、queue置換
- 本体の同意音声→専用Chrome起動→単発検索→発話による復帰と、CAPTCHA時の見え方
- `/api/v1/player-input` はserviceの直列入口であるため、同期中の状態機械leaf生成そのものを途中停止する境界は未実装。入力受理後の音声は止まり、入力は一度だけ次の処理へ進む
- 戦闘後10 turnの自然な再開会話
- 危険前5往復＋危険後3 turnの実会話品質と、家庭音声の純粋な叫声／意味のある発話の境界
- 雑談から会話材料を使った実際の句品質
- 雷／夕方とマイク入力が同時に来た実機動作

## 7. 自動確認

Rustの各ドメインテストと、`dogido-rust/scripts/check_foreground_runtime.py`・`check_history_retention.py`・`check_language_runtime.py`・`check_address_runtime.py`・`check_web_runtime.py` で確認する。次を正負の対で固定する。

- casualでは発句可／learningでは発句時計を凍結
- casualの非敵対ambientは最後の入力から30秒で再開し、再入力で数え直す。学習・Web・川柳集中中の抑止／敵対警告は維持
- 戦闘で一件だけ保留／game tickでは減らない／player turn 10件で失効
- 戦闘中の会話試行あり／なしで終了文を分ける
- 雷・夕方が入力を失わず次tickへ戻す
- stale worker結果、再生失敗、cancel、queue置換を履歴へ入れない
- `completed` だけを会話履歴と川柳材料へ入れる
- 生成取消と実配送結果を分離し、dispatched後の実 `completed` を取消で上書きしない
- 本人barge-inで、再生中actionと取得済みbatch末尾が各一度だけ終端する
- 正本DB回答のplayer側を受理時、assistant側を再生完了時に共有し、次の限定対話が同じturn IDで参照する
- 2分未満の突然の話題を無言保留し、呼び直し→修復質問の再生完了→肯定後に元入力を一度だけ本体へ戻す。2分・5分の境界と期限切れも固定する
- 純粋な音声叫声を通常履歴へ入れず、原因をコード観測だけから状況メモへ落とす
- 危険前5往復を危険後3 player turn目まで限定対話にも共有し、4 turn目で解除する
- 雑談中も情景音声を先に返し、準備中は掛け軸を開かない。130秒／300秒の本句生成後からworkshop期限を数え、失敗・戦闘取消では空のworkshopを残さない
- 音声callbackとgame-event回収の同時実行、満杯のイベント列、重複した完了通知でも終端結果と履歴を壊さない
- Web同意→案内音声の `completed` 後だけ一度起動し、失敗・取消・戦闘・満杯・重複完了では開かない
- Web成功時のforeground所有、通常会話5分を越える読書期限、ambient抑止、復帰時の話題一件だけの受け渡し、session終了時の専用client close

全体回帰は次で確認する。

```bash
./dogido-rust/cargo.sh test --all-targets --locked
```

2026-09-12の最終回帰は **1389 passed、1 skipped、1512 subtests passed**。
