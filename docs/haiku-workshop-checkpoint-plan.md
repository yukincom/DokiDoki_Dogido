# 川柳 workshop チェックポイント記憶（方針）

**日付:** 2026-08-03  
**状態:** 方針確定（実装は未着手・段階的）  
**Issue:** [#37 長期メモリが弱い件](https://github.com/yukincom/DokiDoki_Dogido/issues/37)  
**前段:** [#8 workshop 入り口](https://github.com/yukincom/DokiDoki_Dogido/issues/8)（入り口・drift・詩人 leaf は完了・closed）  
**関連:** [memory-architecture.md](memory-architecture.md) · [haiku-feedback-plan.md](haiku-feedback-plan.md) · [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md) · [senryu-rag-plan.md](senryu-rag-plan.md)

着想メモ（ニューラル置換ではない）: [Memory Caching 系の論文](https://arxiv.org/html/2602.24281v1) の  
「固定窓の限界を、圧縮した過去状態のキャッシュ＋選択参照で伸ばす」思想だけを、**外部 JSON メモリ**に移植する。

---

## 1. キャラクター前提（いちばん大事）

ドギドは **万能の長期記憶付き相棒**にしない。

| 設定 | 意味 |
|---|---|
| **おじさん** | 長く一緒にいるが、何でも覚えているわけではない |
| **趣味の川柳以外は忘れがち** | 冒険の細部・雑談の繰り返しを、セッションを跨いで精密に保持しない |
| **覚えるのは「句の好み・直し」** | workshop で指摘されたこと・直したことが次の発句に薄く効けば十分 |

**長期でマイクラを回して物足りなさを感じたとき**に、雑談・冒険エピソードまで広げるかを考える。  
いまは「思い出してくれない」こと自体をバグにしない。

### やらないこと（当面の非ゴール）

- 冒険・戦闘・村人・バイオーム入場をすべてセグメント化して覚える
- 雑談（player_chat）プロンプトへの checkpoint 注入
- embed 必須のベクトル記憶、ルーター LLM、汎用エージェント基盤
- 状態機械の判定正本をメモリに移す
- 「全部覚えて一貫した人格史」を目指す

既存の短期会話（5往復）・rolling_summary・entries/revisions/lessons は **そのままでよい**。  
本計画は **川柳 workshop 専用の薄い層**を足す話である。

---

## 2. 合意したスコープ

| 項目 | 決定 |
|---|---|
| セグメント | **workshop のみ**（発句〜講評〜閉じ の一連） |
| 圧縮 | **まずテンプレ** → 行が溜まってから任意で LLM 圧縮 |
| 読み出し | **発句（haiku leaf）のみ**。雑談には載せない |
| 判定 | これまでどおり **コード（SM）が正本** |
| 正本形式 | 既存どおり JSON/JSONL。汎用エージェントは使わない |

---

## 3. ため方（蓄積）の提案

### 3.1 いつ1件書くか（セグメント境界）

**1 workshop pin につき最大1件**を基本にする。

| タイミング | 書く？ | 理由 |
|---|---|---|
| **close 時**（明示 / praise / revise 成功 / timeout） | **書く** | 句の話が一区切りついた |
| close 時 reason=`drift` かつ **meaningful turn が1回以上** | **書く** | 添削はあったが話題が流れた |
| close 時 reason=`drift` かつ turn 0 | **書かない** | 中身のない空セグメント |
| close 時 reason=`panic` / `next_haiku` | **meaningful があれば書く** | 次の句に食われただけでも学びは残す |
| open 中の毎ターン | **書かない** | ノイズ。close でまとめる |
| 雑談・戦闘・ambient | **書かない** | スコープ外 |

**meaningful turn** の定義（コードで足りる）:

- `ask_meaning` / `critique_*` / `other_haiku` / `soft_default` で workshop 返事した
- または conversational / formal の直しが保存された  
- のみ `praise` / `close` / `ack` だけ → 好みの情報は薄いので **任意**（最初は「critique または revise があったら必須」でよい）

### 3.2 1件の中身（スキーマ案）

正本パス案（未実装）:

```text
.dogido_memory/long_term/haiku_workshop_checkpoints.jsonl
```

1行 = 1 checkpoint（追記のみ）:

```json
{
  "id": "hwcp_…",
  "created_at": "ISO-8601",
  "session_id": "ses_…",
  "segment_kind": "haiku_workshop",
  "close_reason": "praise|revise|timeout|explicit|drift|next_haiku|panic",
  "verse": "五\n七\n五",
  "entry_id": "h_… or null",
  "tags": {
    "biome": "plains",
    "time_phase": "morning",
    "motifs": ["石炭", "草地"],
    "critique_kinds": ["forced_compress", "other"]
  },
  "player_turns": [
    {"kind": "critique_forced", "text": "黒い石炭がちょっと長いかも"}
  ],
  "essence": "テンプレで組み立てた2〜4文（口語・biome: 等の内部キー禁止）",
  "prefs_soft": ["字余り・長い言い回しは控えめ", "読みやすさ"],
  "source_refs": {
    "critique_ids": ["hcrit_…"],
    "revision": false
  }
}
```

- **`essence` / `prefs_soft`:** 初手は **テンプレート**（プレイヤー文の要約連結 + kind から決めた固定フレーズ）。  
- **LLM 圧縮:** 後からバッチで `essence` を書き換え or 別フィールド `essence_llm` を足す（リアルタイム経路に載せない）。

### 3.3 テンプレ essence の例（LLM なし）

```text
句: そらまぶし / くさむらにうかぶ / くろいせきたん
材料口語: 湿り気
指摘: 「黒い石炭がちょっと長いかも」(字数) / 「黒石炭とかにしたらいい」(言い換え)
閉じ: revise
```

`prefs_soft` は kind マップで機械生成:

| critique / kind | prefs_soft 例 |
|---|---|
| forced_compress / 長さマーカー | 要素を絞る・長い言い回しは控えめ |
| unreadable / gibberish | 読みやすさ・かな連続に注意 |
| off_context | 材料・場面から大きく外れない |
| other / soft_default | （プレイヤー文が短ければ prefs は空でも可） |
| praise | tighten を増やさない（loosen は既存 lesson 経路） |

### 3.4 溜めすぎない（容量）

| ルール | 案 |
|---|---|
| 上限 | 直近 **N=50 件**（または 90 日）を list 時に切る |
| 重複 | 同じ `entry_id` は **最新1件**だけ参照候補に |
| 空 | `player_turns` が空なら行を書かない |
| 個人情報 | 句と講評以外を混ぜない（位置座標・実名は載せない） |

### 3.5 既存ストアとの関係

| 既存 | 役割 | checkpoint との関係 |
|---|---|---|
| `haiku_entries` / `revisions` | 句そのものの正本 | **正本のまま**。checkpoint は要約ビュー |
| `haiku_critiques` / `haiku_lessons` | 講評ログ・軸 soft | checkpoint は **エピソード**。lesson は軸集約のまま |
| `rolling_summary` | 起動用の粗い要約 | **触らない**（おじさん設定。冒険要約を賢くしない） |
| workshop pin | 今の句の短期対話 | close で checkpoint 化してピンは捨てる（現行どおり） |

lesson と checkpoint を両方プロンプトに山盛りしない。  
発句時は **既存 soft lesson（最大数件）＋ checkpoint から選んだ 0〜2 件の essence** 程度。

---

## 4. 読み出し（発句のみ）

### 4.1 いつ読むか

- **haiku 生成**（irony/scene 後の本句 leaf、および repair に載せる制約ブロック）のときだけ  
- player_chat / ambient / panic / workshop 返事自体には **載せない**

### 4.2 どう選ぶか（第1波・メタのみ）

クエリはいまの `HaikuContext` から:

- `biome`（一致を優先）
- `time_phase`（任意）
- 直近 lesson の `lesson_type` と checkpoint の `critique_kinds` の重なり
- 新しさ（新しいほど少し加点）

上位 **0〜2 件**。0 件なら何も足さない（無理に思い出さない＝おじさん）。

### 4.3 プロンプトへの載せ方

既存の `player_lessons` soft 行に近い短いブロック:

```text
【前に句で言われたこと（参考・強制ではない）】
- …
```

- **hard 禁止に合流しない**（AGENTS どおり soft）
- 内部キー名・entry id を口に載せない

### 4.4 第2波以降（需要が出てから）

- `essence` の LLM 再圧縮（非同期）
- 埋め込み類似（カタログ RAG 第2波と **インデックスは分ける**）
- 雑談への注入は **実プレイで物足りなさを感じてから**検討

---

## 5. 実装フェーズ（まだコードを書かない前提の地図）

| Phase | 内容 | 受け入れ |
|---|---|---|
| **M0** | 本ドキュメント + スキーマ合意 | いまここ |
| **M1 書くだけ** | workshop close で jsonl append（テンプレ essence）。プロンプト未接続 | ログで行が増える。空セグメントなし |
| **M2 読む** | 発句 details に 0〜2 件 | 同じ biome で過去の「長い」指摘が soft に見える |
| **M3 任意** | LLM 圧縮バッチ / 容量 GC | 品質がテンプレで足りないときだけ |

リアルタイム経路に重い LLM を足さない。M1 は **純コード**でよい。

---

## 6. 論文対応（参考・実装義務ではない）

| 論文の言い方 | このプロジェクトでの対応 |
|---|---|
| セグメント | workshop 1 回分のみ |
| チェックポイント | `haiku_workshop_checkpoints.jsonl` 1 行 |
| オンラインメモリ | 現状の短期 + ゲーム状況（変更なし） |
| Sparse 選択 | 発句時メタで 0〜2 件 |
| RNN / ニューラル層 | **採用しない** |

---

## 7. Issue 運用

| Issue | 役割 |
|---|---|
| **#8** | workshop **入り口**（soft 既定・詩人 leaf 等）→ **closed** |
| **#37** | **記憶（checkpoint）** の本線。実装・観察はこちら |

実装に入るときは #37 に Plan を書き、go サイン後に M1 から。

---

## 8. 一言まとめ

> ドギドは川柳オタクのおじさん。  
> **句の直しだけ**を、workshop 単位で薄くため、次の発句にだけ思い出す。  
> 冒険の長期記憶は、今は要らない。物足りなくなったらまた考える。
