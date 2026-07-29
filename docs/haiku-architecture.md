# 川柳アーキテクチャ

読み上げの速度・5-7-5 の間・SE 方針は [voice-delivery-plan.md](voice-delivery-plan.md)（Issue #13）。

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
7. `haiku` route で 5-7-5 の一句を生成する（厳格不合格時は repair 1 回）
8. なお音数が外れても **かなの句らしい** 出力は `accepted_imperfect` として出す（workshop で直す前提）。英語・禁止道具・五十音羅列などは従来どおり `まとまらんかった` 系 fallback

### 発話の語順（[voice-delivery-plan.md](voice-delivery-plan.md)）

**実装済み（LLM preface 経路）:**

```text
Frame N:   irony/scene → 見どころ一言 +「ここで一句。」（自分の世界モード開始）
Frame N+1: haiku leaf → 本句（モード解除・workshop 可）
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
- 手持ち
- インベントリ上位
- 周辺ブロック上位
- 平和 mob
- mob の `poetic` tag
- ローカルで組み立てた `candidate_tensions`

LLM には「選ぶ」「まとめる」「詠む」だけをさせる。

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
