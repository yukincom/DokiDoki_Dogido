# 本体の会話所有権・中断・再生確定

**状態:** 2026-09-09、Web／ゲーム一時停止を除く第一段階をコード・自動テスト済み。実Minecraft、実モデル、実音声は未確認。

独立 `language_dialogue` で確かめた国語対話を、本体の状態機械を置き換えずに接続するための境界を定める。戦況、assist、川柳workshop、保存判断は従来どおりコード側が所有する。

## 1. 会話ルートの所有者

session内に、同時に一つだけforeground routeを置く。

| route | 役割 | 非敵対ambient | 新しい自動川柳 |
|---|---|---:|---:|
| `none` | プレイヤー主導の話題なし | 可 | 通常条件で可 |
| `casual` | Minecraft観測を使う既存 `player_chat` | 抑止 | 10分周期で可 |
| `learning` | 正本DBの明示知識回答と、国語・語句の限定対話 | 抑止 | 周期ごと凍結 |
| `web` | Web閲覧中の一時対話 | 抑止 | 周期ごと凍結 |
| `haiku_workshop` | 既存の一句共同編集 | 抑止 | 既存workshop規則で抑止 |

- routeは長期記憶へ保存しない。
- 通常会話の本文履歴は従来の5往復を維持する。
- 国語・語句の明示質問と `learning` 中の続きだけを有界な専用workerで生成し、ゲームイベントの直列workerをLLM待ちで塞がない。正本DBの即答は従来どおり状態機械が所有する。
- 世界操作・敵方向・所持品・明示知識DB・workshop入力は、このworkerへ渡さない。
- workerが満杯なら質問を雑談へ誤配送せず、既存のplayer input待ち列へ一度だけ戻す。

## 2. 再生完了を会話の正本にする

入力、生成した返答、音声再生を `turn_id` と `utterance_id` で結ぶ。

```text
player accepted
  -> reply selected
  -> audio queued
  -> audio started
  -> completed | failed | cancelled
```

- プレイヤー発話は受理時点で短期履歴へ入る。状態機械が返す正本DB回答も同じturn IDで限定対話へ共有する。
- ドギドの返答は、音声プロセスの `completed` を次の直列game eventで回収した後だけ履歴へ入る。
- `failed`、`cancelled`、キュー置換、戦闘で失効した古い結果はassistant履歴や川柳材料へ入れない。
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

川柳workshopの戦闘中断は既存 `haiku/combat_pause.py` が正であり、この一般会話bookmarkへ置き換えない。

## 4. 雷と夕方

- 最初に実測した雷鳴／近距離落雷は現在のplayer replyより先に割り込み、同じ入力を次tickへ戻す。会話routeと履歴は消さない。
- 以後は既存のcue 10分・一言3分のcooldownを使い、再度話す場合は非割り込みの小さな懸念にする。
- 「player_chatを返したので雷を3分無視する」という旧規則は使わない。
- 地表夕方の既存警告も同じ入力を次tickへ戻す。洞窟・水中・安全な屋内の既存除外と、一晩一回の規則は維持する。

## 5. 雑談中の自動川柳

雑談が続いても発句を永久に止めない。

- 通常の10分周期が来ても、現在のマイク入力やplayer replyを途中で切らない。
- player replyがある境界ではその返答の後ろ、そうでなければ次の安全なstatus／音声キュー境界へ、非割り込みの固定導入
  「あっ……ちょっと待って。なんか、浮かんできたかもしれん……。」
  を置く。
- 再生完了済みの直近3 turnだけから、目標40〜60字・最大80字の短い会話材料、最大3語のmotif、元turn IDを作る。
- 会話材料は `player_reported_context` として元turn IDへ結ぶsoft材料であり、Minecraft世界の実測事実やhard制約へ昇格しない。
- 発句開始時は雑談を `suspended` に置き、既存workshopをforegroundにする。
- `learning` と `web` の間は発句間隔そのものを凍結する。解除直後に抑止時間分をまとめて経過扱いにしない。
- 発句準備中に危険が来た場合は、既存規則どおり古いprompt・材料・pendingを破棄する。

## 6. まだ接続していない境界

### WebとMinecraft一時停止

本体 `MainLanguageRuntime` には現在Web providerを渡していない。独立試験のWeb同意・15秒単発読み取りを、そのまま本体へ有効化してはいない。

共有contractは未合意のため、Fabric／server双方へまだ実装しない。現在の**提案**は次のとおり。

- execution capability: `client.game.pause.v1`
- server command: `pause_game` のみ
- 実pause成功ackを受け取るまでWebを開かない
- Dogidoからの `unpause` commandは設けず、再開はユーザー操作だけ
- multiplayerやpause非対応環境ではfail closedし、Webを開かない

このID・HTTP外形は提案であり、合意後に `adapter-api`、モデル、Fabric実装、ack、失敗／再起動試験を同時に追加する。

### 実機確認

未確認なのは次の範囲。

- 実Minecraftのevent列と実LLMの遅延を重ねた応答順
- 実TTSの完了callback、割り込み、queue置換
- 戦闘後10 turnの自然な再開会話
- 雑談から会話材料を使った実際の句品質
- 雷／夕方とマイク入力が同時に来た実機動作

## 7. 自動確認

中心は `tests/test_main_dialogue_integration.py`。次を正負の対で固定する。

- casualでは発句可／learningでは発句時計を凍結
- foreground中は非敵対ambient抑止／敵対警告は維持
- 戦闘で一件だけ保留／game tickでは減らない／player turn 10件で失効
- 戦闘中の会話試行あり／なしで終了文を分ける
- 雷・夕方が入力を失わず次tickへ戻す
- stale worker結果、再生失敗、cancel、queue置換を履歴へ入れない
- `completed` だけを会話履歴と川柳材料へ入れる
- 正本DB回答のplayer側を受理時、assistant側を再生完了時に共有し、次の限定対話が同じturn IDで参照する
- 音声callbackとgame-event回収の同時実行、満杯のイベント列、重複した完了通知でも終端結果と履歴を壊さない

全体回帰は次で確認する。

```bash
python -m pytest -q
```

2026-09-09の最終回帰は **1254 passed、1 skipped、1496 subtests passed**。
