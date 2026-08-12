# 川柳アーキテクチャ

読み上げの速度・5-7-5 の間・SE 方針は [voice-delivery-plan.md](voice-delivery-plan.md)（Issue #13）。

## プレイヤー指摘の薄い記憶

workshop の講評を次の発句に効かせる方針は [haiku-workshop-checkpoint-plan.md](haiku-workshop-checkpoint-plan.md)（Issue #37）。  
**読み出しは発句のみ。** 雑談・冒険の長期一貫性は今は狙わない。

## レイテンシ方針

- 緊急度高 / 低レイテンシ
  - 戦況報告・警告
  - state machine + ルールベース
  - LLM は使わない
  - キャッシュ音声と短い callout を優先する

- 緊急度中 / 普通のレイテンシ
  - 雑談・状況解説・助言
  - `chat` route の LLM を使う
  - ローカル MLX でもクラウド API でもよい

- 緊急度低 / レイテンシ許容
  - 川柳
  - まずコード側で状況候補を絞る
  - `chat` route で軽い矛盾抽出を行う
  - `haiku` route で最終の一句だけを生成する

## 現在の川柳フロー

1. 敵性 mob がいない
2. 特殊バイオーム注意喚起が保留されていない
3. プレイヤー入力や他の発話で沈黙が破られていない
4. 一定時間静かだったら川柳候補を起動
5. コード側で `HaikuContext` を組み立てる
6. `chat` route で `IronyContext` / scene を JSON 抽出する
7. 観測 ID からカタログ原文・実測の `source atom` を作り、実際に発話した見どころも別種の解釈 atom にする
8. `haiku` route（初稿 temperature `0.60`）で、かな三行の JSON を生成する
9. `chat` route（temperature `0.0`）で、一行ごとに「どの atom の意味が残ったか」「自然な日本語か」を JSON 判定する
10. コードで、かな・音数・道具 hard 制約・出典 ID・行間の atom 重複を検証する
11. 合格行を固定し、不合格行だけを `haiku` route（temperature `0.30`）で再生成する。コードで数えた現在音数・目標音数・許容範囲を各対象行へ渡す。最大 2 回
12. 2 回後も一行でも不合格なら、句全体を発話せず `まとまらんかった` 系 fallback に閉じる。失敗句は workshop pin・長期保存の対象にしない

### カタログ原文と source atom

保存済みカタログ JSON は、通常会話と将来の **「ドギドのあんちょこ」** が読む知識の正本でもある。川柳の都合で `note: str` を配列へ変えたり、元 JSON へ atom を書き戻したりしない。

- `japanese` / `note` の原文は変更しない
- 発句時に観測された ID だけを `catalog_type:catalog_id` へ結び、原文 snapshot を作る
- `note` は実行時だけ `。！？!?` の直後で分ける。読点 `、` では分けず、終止記号も原文に残す
- 名前、各 note 文、mob の `poetic` 原文フィールド、実測した時刻・天気・場所・手元・周辺物・mob を、追跡可能な atom として扱う
- irony / scene の内部要約をそのまま出典にはしない。ただし、見どころとしてプレイヤーへ**実際に発話した文**は読点・句点単位で `preface_interpretation` atom にし、句がその意味を受け取ることを許す
- カタログ名は全文の復唱を要求しない。意味保持の検証でその行の出典と確定した `catalog_label` に限り、ラベル中の4文字以上のかな語と一字だけ違う断片をコードで訂正する（例: `シラカバの階段` を根にした `しろかばの` → `しらかばの`）。全カタログへの曖昧検索、短語、複数候補、挿入・削除は自動訂正しない
- `preface_interpretation` はカタログ事実・実測観測へ昇格させず、`preface:spoken` という別 provenance で保存する。短縮で発話から落ちた連想は atom にしない
- 生成済みの各行には採用した atom ID と原文 snapshot を添え、`haiku_entries.jsonl` の `materials_snapshot` に保存する

たとえば古代の残骸の `note` は、原文を保持したまま実行時だけ次の三要素になる。

1. `ネザーに生成される珍しい鉱石。`
2. `茶色で、ひび割れている。`
3. `しかし、非常に高い爆発耐久値を持っており熱に強い。`

同じ要素を複数行の主な出典にはしない。先に合格した行が使った atom は予約し、後続行の再生成候補から除外する。カタログに `note` がない物について、名前から性質や音を推測して atom を捏造しない。

意味の言い換えと日本語の自然さは LLM が判定するが、候補外 ID の拒否、重複排除、最大試行回数、発話・保存の可否はコードが決める。固定語の字面一致で reject する旧 H6 は復活させない。

workshop は、この発話前ゲートを通った句に対する好み・表現・場面違和感を一緒に扱う場所である。壊れた句を先に出して workshop に修理を任せる品質ゲートではない。

### 発話の語順（[voice-delivery-plan.md](voice-delivery-plan.md)）

**実装済み（LLM preface 経路）:**

```text
Frame N:   irony/scene → 見どころ一言 +「ここで一句。」（自分の世界モード開始）
Frame N+1: 構造化発句 + 行別検証 → 本句（モード解除・workshop 可）
```

- 見どころは irony description / scene summary を短くし、「…が頭に浮かんできたわ。」にする  
- 材料全文の棒読みはしない  
- **自分の世界モード**（`pending_haiku_after_preface`）: player_chat に乗らない。入力はキュー保持。脅威はキャンセル可  
- **川柳フォーカス**（`_haiku_focus_active` = preface 待ち **or** workshop pin open）:

| 種別 | フォーカス中 |
|---|---|
| panic / alert / 悲鳴 / 脅威 callout | **許可**（敵ターゲティング等） |
| 暗所押し・閉塞暗所の入口 | **許可**（足元の安全） |
| 水没暗所コメント | **抑止**（敵が出にくいゾーン） |
| 夜警告 | **抑止**（pending 保持 → pin close 後） |
| ambient モブ・バイオーム入場・構造物・蛍・マグマ足元・天候など | **抑止** |
| workshop 講評 / player_chat | service / chat 経路で処理 |

カタログ fallback（LLM 無し）は従来どおり「ここで一句。 句」の一発。

## Snapshot A の考え方

LLM に event 全体を丸投げしない。

コード側で先に以下を抜き出す。

- バイオーム
- バイオーム分類
- 地形メタデータ
- 時間
- 天気
- 現在 Y、バイオーム基準気温、降雪開始高度からコードで確定した現在地の雨／雪
- 周辺の `snow` / `snow_block` / `powder_snow` 実測（地表の積雪根拠）
- 手持ち
- インベントリ上位
- 周辺ブロック上位
- 平和 mob
- mob の `poetic` tag
- ローカルで組み立てた `candidate_tensions`

LLM には「選ぶ」「まとめる」「詠む」だけをさせる。

降雪開始高度だけを creative な source atom にしない。コードが現在 Y と比較し、
実際に雪が降っている場合は「降雪」、周辺の雪ブロックを観測した場合は「積雪」を
材料にできる。晴天かつ実ブロック未観測なら、寒冷地名だけから地表の雪を
現在場面として断定しない。この確定情報は player chat と同じ判定を共有する。

## 外部検索について

現時点の baseline 川柳は、ローカルの `entries` / `mobs` / `biome` カタログを優先する。

- wiki 参照は必須にしない
- 外部検索も baseline では使わない
- JSON カタログが足りないときだけ後で検討する

これにより、毎回の句生成でネット依存にならないようにする。

## VLM の扱い

VLM はまだ常時使わない。

将来は以下の条件で使う予定:

- プレイヤーが「一句詠んでほしい」と明示的に依頼したとき
- プレイヤー入力 layer が実装され、意図判定が安定してから

つまり、今は未実装の先送り項目とする。

## API / SDK 方針

クラウド LLM は SDK ではなく HTTP API を直接叩く。

理由:

- configUI から provider 切り替えしやすい
- route ごとに model / provider を差し替えやすい
- 依存ライブラリを増やさずに済む

対応対象:

- OpenAI
- OpenRouter
- Claude
- Grok
- Gemini

## エージェント / オーケストレーション方針

- 川柳・雑談の**保存はコード**（`.dogido_memory/` の JSONL）が正本
- 汎用エージェント基盤（例: Hermes）は**使わない**。編集者エージェント一式は機能過剰
- 将来、irony 抽出 → 一句生成 → fallback のような LLM ワークフローを整理するときは **LangChain / LangGraph** で必要なグラフだけ載せる想定
- panic / cue / 状態遷移は引き続きコード側（状態機械 + py_trees）が担当し、LangGraph に移さない
