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
6. `chat` route で `IronyContext` と、発話候補を1〜3節に分けた scene JSON を抽出する。各節は一次 atom ID、`factual` / `interpretive` を申告する
7. 別の `chat` structured 評価で、各節の意味保持・分類・主張範囲・自然さを一次 atom へ照合する。全節が通った場合だけ発話し、同じ節契約から `preface_clause` atom を作る
8. 設定で固定した生成方式を添え、`haiku` route（初稿 temperature `0.60`）で、かな三行の JSON を生成する
9. `chat` route（temperature `0.0`）で、一行ごとに「どの atom の意味が残ったか」「自然な日本語か」を JSON 判定する
10. コードで、かな・音数・道具 hard 制約・出典 ID・一次 atom 単位の行間重複を検証する
11. 不合格行を含む**生成スロット**だけを `haiku` route（temperature `0.30`）で再生成する。各対象行へコードで確定した失敗理由・現在音数・目標音数・許容範囲を渡す。実測中は最大 6 回（設定上限 8 回）
12. 空白・句読点・カナ種だけが違う既出候補はコードで即時棄却し、意味評価を再実行しない
13. 6 回後も一行でも不合格なら、句全体を発話せず `まとまらんかった` 系 fallback に閉じる。失敗句は workshop pin・長期保存の対象にしない

### 4方式の比較実験

同じ source atom、同じプロンプト温度、同じ発話前検査器を使い、**生成・再生成をまとめる単位だけ**を比較する。現在は `DOGIDO_HAIKU_GENERATION_STRATEGY` で一方式ずつ固定する。材料やプレイヤー嗜好からの自動選択はまだ行わない。

| 設定値 | 生成スロット | 狙い |
|---|---|---|
| `whole_poem` | `[上五・中七・下五]` | 三行全体の流れを優先。どこか一行が不合格なら一句全体を再生成 |
| `three_slot` | `[上五] [中七] [下五]` | 三つの異なる観察点を組み合わせ、合格行を個別に固定 |
| `one_plus_two` | `[上五] [中七・下五]` | 上五に入口や印象を置き、後二行を一続きに展開 |
| `two_plus_one` | `[上五・中七] [下五]` | 前二行で場面を作り、下五を独立した着地にする |

一つのスロット内で一行でも不合格なら、そのスロットの全行を作り直す。別スロットの合格行は固定し、使用済み atom を候補から除く。生成方式名、実際の再生成 round 数、`prompt_variant` はログと `materials_snapshot` に残すため、方式別の成功率・失敗理由・時間・人間評価を後から比較できる。

自動 `StrategySelector` はこの固定比較の後に載せる。最初から状況・好み・直近評価で方式を切り替えると、方式そのものと selector の誤りを分離できないためである。固定比較後も、選択はコードが担当し、LLM に方式決定や preference 更新を委ねない。一般的な praise / critique をそのまま方式への加減点にはせず、句の品質と方式の相関を実測してから、明示的な形式の好みと限定された状況帯だけを薄く使う。

`generation_strategy` を既存の strategy ID とし、別名フィールドは増やさない。発句 entry と workshop critique の `materials_snapshot` が `generation_strategy` / `prompt_variant` を引き継ぐので、同じ講評を句・生成方式・プロンプト版へ後から結び直せる。

### 一時的な実環境設定（戻し忘れ禁止）

2026-08-13 の比較実験では、データ収集を速めるため発句間隔を通常の 10 分から **3 分**（`180000ms`）へ一時短縮している。脅威・プレイヤー入力・他発話後の 30 秒静寂は維持する。

> **比較・開発が終わったら `DOGIDO_HAIKU_INTERVAL_MS=600000`（10分）へ必ず戻す。** `config.py` の既定値と `.env.example` の例も同時に戻し、この節を実験終了へ更新する。

### カタログ原文と source atom

保存済みカタログ JSON は、通常会話と将来の **「ドギドのあんちょこ」** が読む知識の正本でもある。川柳の都合で `note: str` を配列へ変えたり、元 JSON へ atom を書き戻したりしない。

- `japanese` / `note` の原文は変更しない
- 発句時に観測された ID だけを `catalog_type:catalog_id` へ結び、原文 snapshot を作る
- `note` は実行時だけ `。！？!?` の直後で分ける。読点 `、` では分けず、終止記号も原文に残す
- 名前、各 note 文、mob の `poetic` 原文フィールド、実測した時刻・天気・場所・手元・周辺物・mob を、追跡可能な atom として扱う
- irony の内部要約をそのまま出典にはしない。scene は自由な `summary` を受けず、実際に話す1〜3節を structured で返す。各節は元になった一次 atom ID と `factual` / `interpretive` を持つ
- `factual` の主張範囲はコードが一次 atom から継承する。`identity_only` は名称、`source_meaning` は原文の意味、`observed_state` は現在の実測まで。`interpretive` は `poetic_interpretation`（印象・取り合わせ）だけで、新しい事実の断言には使えない
- scene生成とは別の評価で、意味保持・分類・主張範囲・自然さが全節とも通った場合だけ発話する。検証済み節は `preface_clause` atom として保存し、派生元の `basis_atom_ids` を失わない
- カタログ名は全文の復唱を要求しない。意味保持の検証でその行の出典と確定した `catalog_label` に限り、ラベル中の4文字以上のかな語と一字だけ違う断片をコードで訂正する（例: `シラカバの階段` を根にした `しろかばの` → `しらかばの`）。全カタログへの曖昧検索、短語、複数候補、挿入・削除は自動訂正しない
- `preface_clause` は `preface:spoken` という別 provenance で保存する。句の行間重複を判定するときは派生 atom の ID ではなく `basis_atom_ids` を予約し、同じ一次材料を直接／見どころ経由で二重利用させない
- 生成済みの各行には採用した atom ID と原文 snapshot を添え、`haiku_entries.jsonl` の `materials_snapshot` に保存する

この変更では旧契約との入力互換を残さない。scene の旧 `summary`、旧 `preface_interpretation`、`claim_class` / `claim_scopes` / `basis_atom_ids` を欠く保存 atom、行 grounding の旧単体 object は受け取らず fail-closed にする。過去の句本文・講評は残るが、出典契約を証明できない旧句から自動修正案は作らない。

たとえば古代の残骸の `note` は、原文を保持したまま実行時だけ次の三要素になる。

1. `ネザーに生成される珍しい鉱石。`
2. `茶色で、ひび割れている。`
3. `しかし、非常に高い爆発耐久値を持っており熱に強い。`

同じ要素を複数行の主な出典にはしない。先に合格した行が使った atom は予約し、後続行の再生成候補から除外する。カタログに `note` がない物について、名前から性質や音を推測して atom を捏造しない。

意味の言い換えと日本語の自然さは LLM が判定するが、候補外 ID の拒否、一次材料の重複排除、生成スロットの展開、最大試行回数、発話・保存の可否はコードが決める。再生成へ返す失敗理由は `meaning_not_retained`、`unnatural_japanese`、`source_reused`、音数／hard制約違反、候補重複などの閉じたコード値である。同一候補は Unicode 正規化・カナ統一・空白／句読点除去後のsignatureで即時棄却する。意味の近さを固定語リストで判定する旧 H6 は復活させない。

workshop は、この発話前ゲートを通った句に対する好み・表現・場面違和感を一緒に扱う場所である。壊れた句を先に出して workshop に修理を任せる品質ゲートではない。

### workshop の Locate → Edit → Test

三行だけの川柳では、コード用ASTや汎用サブエージェントを持ち込まず、**一行を構造ノード**として扱う。役割は呼び出しとコード境界で分離する。

1. 限定抽出AIが、プレイヤーの発話から対象行・一意な断片・問題種別を locate する
2. コードが対象行を確定し、固定行・使用可能atom・発句時hard制約を閉じる
3. haiku route の編集AIは全文ではなく、`line_index` / `expected_text` / `replacement_text` / `atom_ids` の差分だけを返す
4. コードが `expected_text` と現在の元行の完全一致、対象外行の不変、候補の実変更、一意な出典を確認する
5. 別structured評価と共通検査器が、意味保持・自然さ・音数・hard制約・出典重複を確認する
6. 合格案も未保存の `pending_revision` に置き、明示採用時に同じ差分が同じ元句へ適用できるか再確認してから保存する

これは行単位の compare-and-swap であり、対象が0件・複数解釈・元行不一致・一部だけ成功のときは元句を維持する。旧 `{line_index, text}` 応答は受け付けない。採用済み revision には `edit_contract` と検証済み `edits` も残し、後から局所修正の成功傾向を監査できるようにする。

### 発話の語順（[voice-delivery-plan.md](voice-delivery-plan.md)）

**実装済み（LLM preface 経路）:**

```text
Frame N:   irony/scene → 見どころ一言 +「ここで一句。」（自分の世界モード開始）
Frame N+1: 構造化発句 + 行別検証 → 本句（モード解除・workshop 可）
```

- 見どころは検証済み scene 節を順に連結し、「…なんか浮かんできたわ。」にする。発話後の再分割・恣意的な短縮はしない
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
- 現在 Y、バイオーム基準気温、降雪開始高度からコードで確定した現在地の降水なし／雨／雪・雷・降雪環境（数値は LLM へ渡さない）
- 周辺の `snow` / `snow_block` / `powder_snow` 実測（地表の積雪根拠）
- 手持ち
- インベントリ上位
- 周辺ブロック上位
- 平和 mob
- mob の `poetic` tag
- ローカルで組み立てた `candidate_tensions`

LLM には「選ぶ」「まとめる」「詠む」だけをさせる。

Y・Z 座標、気温、降雪開始高度、`downfall` は creative な source atom やプロンプトへ
出さない。`downfall` は降水確率ではなく、コード内部の気候判定にだけ使う。コードが現在 Y と比較し、
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
