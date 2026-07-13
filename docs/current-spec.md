# 現行仕様

この文書は、会話中に固まった方針をまとめた現行仕様です。

- 骨格: 2026-05-24
- スコープ・暗所・LLM 方針の更新: 2026-07-09

既存のメモより優先して参照する前提です。

## 1. 目的とスコープ

- Minecraft のプレイ状況を読み取り、怖がりな AI キャラクター「ドギド」が実況・警告・雑談を行う
- まずは 1 人プレイ前提で設計する
- 他プレイヤーがいても、基本的には接続中のメインプレイヤーだけを観測対象にする
- コンパニオン bot は作らない

### いま固める対象 / 後回し

| 優先 | 対象 | メモ |
|---|---|---|
| **今** | ゲーム状況に反応する対話（警告・実況・雑談・川柳） | 状態機械 + py_trees + LLM leaf |
| **今** | PC 上の音声出力 | VOICEVOX / say / afplay |
| **後回し** | M5Stack Push Avatar | 出力デバイス差し替え。対話設計が固まってから |
| **後回し** | LINE / Discord・定時お知らせ等の外部メッセージ | 入力チャネル追加。対話設計が固まってから |
| **使わない** | Hermes などの汎用エージェント基盤 | 機能過剰。将来の LLM ワークフローは LangChain / LangGraph を想定 |

## 2. システム構成

### 基本方針

- サーバーは本リポジトリの `dogido_server` として実装する
- `yuno-chan-api` は音声入力（whisper 周り）の参考にはするが、定時お知らせや LINE / Discord 受信の取り込みは**対話設計完成後**に再検討する
- `mindcraft` はそのまま使うのではなく、取得できる情報やコード構成の参考に使う
- Minecraft 側は AI bot ではなく、プレイヤー本人を観測するクライアントアダプタとして実装する

### 推奨構成（現行）

```text
Minecraft Java Edition
  -> Minecraft client adapter (Fabric client mod)
  -> dogido-server (Python / FastAPI)
     -> event normalizer
     -> state machine
     -> py_trees action policy
     -> LLM response generator（必要なときだけ）
     -> audio router
  -> PC 音声 (VOICEVOX / say / afplay)
```

### 将来構成（対話設計が固まってから）

```text
  ... dogido-server audio router ...
  -> M5Stack Push Avatar（低遅延 cue + TTS 再生）
  -> （任意）外部メッセージ入力 (LINE / Discord 等)
```

### 責務分担

#### Minecraft client adapter

- プレイヤー本人の状態を取得する
- ゲーム固有イベントを検出する
- `dogido-server` にローカル送信する
  - 受信 endpoint の正本は [受信 API 仕様](adapter-api.md)

#### dogido-server

- イベントを正規化する
- ドギドの内部状態を管理する
- 発話優先度を判定する
- 緊急音声と通常会話を分離する
- 必要なときだけ LLM を呼ぶ

#### 音声出力（現行: PC / 将来: M5Stack）

- 絶叫系 cue はキャッシュ音声を低遅延再生する
- 通常の状況説明は TTS を使う
- M5Stack は上記の再生先の候補であり、現状の必須経路ではない

## 3. 観測ポリシー

将来的に他ゲームにも展開したいが、チートツールにはしない。

そのため、取得情報は「プレイヤーが知り得る情報」に寄せる。

### 取得してよい情報

- プレイヤー本人の位置
- 視線方向
- 体力
- 空腹
- 所持品
- 近くで見えているエンティティ
- 周辺ブロック
- 周辺資源候補
- 時刻
- 天候
- 被弾
- 死亡要因
- クライアントが受け取った音情報

### 強く制限する情報

- 未観測領域の完全な透視
- 壁の向こうの敵の正確な座標断定
- 音だけで得た未視認敵の詳細断定
- 他プレイヤーの隠れた所持品

### 情報の確度レベル

#### `visual`

- 見えている敵や mob
- もっとも確度が高い

#### `auditory`

- 音だけで存在を感じた脅威
- 方向は出してよいが、断定は抑える

#### `inferred`

- 暗い空間の広がり
- 周辺資源の傾向
- 湧きそうな場所
- 推定情報として扱う

## 4. Minecraft 側で取りたい情報

### プレイヤー状態

- `player_name`
- `position`
- `yaw`
- `pitch`
- `health`
- `hunger`
- `inventory`
- `held_item`
- `dimension`

### ワールド状態

- `time_of_day`
- `weather`
- `biome`
- `local_light`
- `sky_visible`
- `ceiling_height`
- `enclosure_score`

### 周辺状況

- 視認できている敵
- 視認できている平和 mob
- 周辺資源候補
- 最近の被弾
- 死亡原因
- モンスターの音
- ブロック破壊の方向と対象

## 5. 音由来の脅威検知

### 基本方針

- プレイヤーの後ろや左右からの危険は分かったほうが面白い
- ただし、未視認敵の完全特定にはしない

### 個別仕様

- スケルトンは、音で `完全察知` させず `たまに予感だけする` 扱いにする
- 正本は [スケルトン仕様](skeleton-spec.md) を参照する

### 表現ルール

- 視認済みなら具体名を出してよい
  - 例: 「後ろ！クリーパーが近づいてくるで！」
- 音だけなら存在と方向中心にする
  - 例: 「左奥からなんか声するで」
- 以前視認していた敵が壁向こうに消えた場合は記憶ベースで言ってよい
  - 例: 「さっきのウィッチ、向こうにまだおるかも」

### 方向粒度

- 前
- 後ろ
- 左
- 右
- 上
- 下
- 左奥
- 右奥

### 実装指針

- 音イベントは粗い方向へ量子化する
- 音だけで得た情報は `certainty=low` として扱う
- 出力時は断定語を避ける

## 6. 暗さと危険度の判定

暗さは単純な明るさ判定ではなく、危険度として扱う。

巨大洞窟のように、足元だけ明るくても暗闇とつながっている場合があるため。

### 経緯（イベント名よりスコア + 多段状態へ）

初期案では暗さ変化を `danger_darkness_changed` 専用イベントで送る想定だった。

実プレイでは「変化の瞬間だけ」では挙動が粗く、

- どれくらい危ないか（連続スコア）
- 暗い空間への侵入（occluded entry）
- 松明なしの押し込み（dark_push の段階・息・あと押し）
- 簡易シェルター入退場
- 水中・葉陰・朝の解除

などをまたいだ多段リアクションが必要になった。

そのため現行は次の方針に寄せている。

- **正**: adapter が `status_snapshot` 等に `danger_darkness_score` や関連フィールドを載せ、server 内で継続判定する
- **副**: `danger_darkness_changed` はスキーマ互換・テスト用に残してよいが、本番 adapter の主経路にはしない

### 判定に使う要素

- `local_light`
- `connected_dark_volume`
- `nearest_dark_spawn_distance`
- `recent_hostile_audio`
- `recent_hostile_visual`
- `enclosure_score`
- `sky_visible`
- `ceiling_height`
- `danger_darkness_score`（総合危険度。最終判断の優先参照）

### `enclosure_score` の意味

- 屋内や地下っぽさの補助指標
- 素材密度だけで最終判定はしない

### `connected_dark_volume` の意味

- プレイヤー周辺とつながっている暗い空間の広がり
- 局所的に明るくても、先に暗い空間が広がるなら危険とみなす

### 判定方針

- `暗い` と `危ない` は別概念にする
- 松明を促す判断は、単なる照度より湧きリスクを優先する
- 反応は単発コールアウトにせず、`dark_push` / shelter などの内部状態で連続的に扱う

## 7. 音声出力方針

### 緊急音声

- 絶叫系悲鳴は TTS ではなくキャッシュ音声を使う
- 現行: PC 上の cue ファイル（`cue_voice/`）を afplay 等で再生
- 将来（M5Stack）: SD カード上のキャッシュ音声を低遅延再生し、サーバーは `cue_id`, `priority`, `interrupt` などの再生命令だけを送る想定

### 通常音声

- 状況説明
- 助言
- 雑談
- 死亡時フォロー

これらは通常の TTS を使う（現行は VOICEVOX / say など）。

### 優先順位

1. 悲鳴
2. 短い警告
3. 通常 TTS

### 割り込み

- 通常会話中でも悲鳴は割り込む
- TTS 再生中でも中断して悲鳴を優先する

## 8. ドギドの状態機械

正本は [状態機械](state-machine.md) を参照する。

### 状態一覧

- `normal`
- `alert`
- `panic`
- `suppressed_panic`
- `aftermath`

### ボス戦の例外

- 一部のボス戦は、通常モンスターと同じ `panic` 連打にはしない
- ボスごとに `panic_policy` を持たせ、`reveal_only` や `tactical` を選べる形にする
- 個別仕様は [ボス仕様](boss-spec.md) を参照する

## 9. `うるさい` 抑制ルール

### 検知対象

- うるさい
- 静かにして
- 黙れ

まずはルールベースで検知する。

### 発動条件

- 3 回以上言われたら発動

### 発動後の挙動

- `panic` の絶叫系悲鳴を 5 〜 10 秒だけ抑制する
- 抑制中は弱い悲鳴に変更する
  - 例: `ハァハァ`, `ひっ・・・・`
- それでも交戦が続いている場合は、状況解説寄りへシフトする
  - 例: `後ろ！`, `あと 2 体！`

### 抑制解除

- 一定時間経過後
- もしくは戦闘終了後

## 10. 初期パラメータ案

詳細は [状態機械](state-machine.md) を参照する。

以下は初期値の要約であり、実機調整を前提とする。

- 緊急視認警告距離: `8` マス
- `panic` 移行距離: `7` マス
- 複数敵警戒距離: `10` マス
- 交戦終了候補距離: `10` マス
- 交戦終了確認時間: `4` 〜 `5` 秒
- 抑制継続時間: `5` 〜 `10` 秒
- 余韻状態時間: `8` 秒前後
- 悲鳴クールダウン: `1.0` 〜 `1.5` 秒

## 11. 戦闘判定

詳細は [状態機械](state-machine.md) を参照する。

### `panic` に入る条件候補

- 敵が `7` マス以内
- 敵が `10` マス以内に 2 体以上
- 直近 3 秒以内に被弾

### 戦闘終了条件候補

- `10` マス以内に敵がいない
- 直近 `4` 〜 `5` 秒で被弾していない
- 直近 `4` 〜 `5` 秒で敵の音や視認更新がない

## 12. 地形・状態の例外

- 水辺、燃焼、粉雪のように `同じ敵でも状況で脅威度が変わる` ケースは別ルールで扱う
- 正本は [環境由来の敵仕様](environmental-hostile-spec.md) を参照する

## 13. 最小イベントスキーマ案

正本は [イベントスキーマ](event-schema.md) を参照する。

### 最小構成の考え方

- `event`
- `player`
- `world`
- `visual_threats`
- `auditory_threats`
- `inventory`
- `combat`

## 14. 受信 API

adapter から `dogido-server` へ送る endpoint の正本は [受信 API 仕様](adapter-api.md) を参照する。

## 15. 抽象化方針

将来的に他ゲームへ展開できるよう、抽象化できるものは抽象化する。

ただし、すべてを完全汎用にはしない。

### 抽象イベントの例

- `threat_detected`
- `threat_approaching`
- `visibility_low`
- `resource_option_found`
- `player_died`

### Minecraft 固有情報の例

- `entity_type=creeper`
- `sound_event=entity.witch.ambient`
- `block_context=deepslate_cave`

### 公開/非公開の切り分け方針

- OSS で出しやすいもの
  - Minecraft adapter
  - event schema
  - 基本 state machine
- 差別化要素として閉じやすいもの
  - 他ゲーム adapter
  - 高度な threat scoring
  - 予測ロジック

## 15. 実装状況と次の優先度

### 完了済み（骨格）

1. Minecraft adapter が送るイベント JSON を定義する
2. adapter -> server の受信 API を定義する
3. `visual / auditory / inferred` の扱いを明文化する
4. state machine を仕様化する
5. audio cue 一覧と優先度を定義する（PC 再生まで）

### 進行中 / 優先して磨く

- 対話品質（プレイヤー入力への反応、状況コンテキスト、ハルシネーション抑制）
- 暗所・戦闘・ボスなどの実プレイ調整
- 川柳フローの安定化（保存は JSONL。将来の LLM グラフは LangChain / LangGraph 想定）

### 対話設計が固まってから

- M5Stack Push への出力差し替え
- LINE / Discord・定時お知らせなど外部メッセージ連携（`yuno-chan-api` 流用可否の再検討を含む）
