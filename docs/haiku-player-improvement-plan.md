# 川柳: プレイヤー主導の改善設計

**日付:** 2026-07-16  
**状態:** 方針・詳細計画（実装前）  
**関連:** [haiku-feedback-plan.md](haiku-feedback-plan.md)、[senryu-roadmap.md](senryu-roadmap.md)、[senryu-rag-plan.md](senryu-rag-plan.md)、[haiku-architecture.md](haiku-architecture.md)

---

## きっかけ（実ログ要約）

| 段階 | 内容 |
|---|---|
| irony / scene | **平原の村・朝・銅のドア・オーク材** など妥当な材料 |
| 発句 | `あさひさす うみに ぐうの きのみづ`（海・不明語。材料と不一致） |
| プレイヤー | 「グーの木の水って何?」「無理やりすぎるんじゃない圧縮の度合いが」 |
| chat | 一般論の俳句談義になり、**その句の材料・直し・学習**に繋がらない |

**方針:** 生成器をこちらで即直すより、**プレイヤーが自然に直せる・教えられる**設計を先に固める。  
（生成品質の自動改善は、プレイヤーが残した材料を効かせる形で後から効く。）

---

## 既存でできること / 足りないこと

### ある（形式的フィードバック）

| 経路 | 内容 | 限界 |
|---|---|---|
| `直し: 五 / 七 / 五` | 元句＋直しを revision 保存 | **プレフィックス必須**。雑談では発火しにくい |
| 読み訂正 | 草地→くさち 等 | **語の読み**向き。句全体の破綻には弱い |
| 今の句保存 / 自動保存 | entries に残る | 保存するだけで **次回の制約にならない** |
| 句思い出して | 検索読み上げ | 改善ループではない |

### ない（今回欲しい）

| 缺口 | 例 |
|---|---|
| **自然言語の講評** | 「ぐうのきのみづって何」「無理やり圧縮」 |
| **句の問題分類** | 読めない / 場面と違う / 詰め込み / いい句 など |
| **材料との突合** | irony/scene は平原なのに句は「うみ」→ 材料無視 |
| **次回への教訓** | few-shot 常駐ではなく soft lesson（癖・好み）。hard 禁止は道具・読みのみ |
| **その句についての対話モード** | 発句直後〜数分、「この句の話」と分かる |

---

## 設計目標

1. プレイヤーは **普段の口調**で句を突っ込み・直せる（専用コマンドは補助）  
2. システムは講評を **構造化して保存**し、次回以降の発句に **薄く**効かせる  
3. 発句プロンプトに履歴を常時詰め込まない（[haiku-feedback-plan](haiku-feedback-plan.md) と同じ：常駐しない）  
4. 対話（player_chat）と川柳の境界を明確にしつつ、**発句直後は「句モード」に入れる**  

---

## 全体像

```text
発句 (irony → scene → haiku)
  → 自動保存 entry + スナップショット materials（irony/scene 要約）
  → 「直近句」をセッションに保持（workshop 対象）

プレイヤー発話
  ├─ 句に関する講評・質問・直し  → haiku_workshop 経路
  │     → critique 構造化 → 保存 → 短い返事
  │     → （任意）一緒に直し案を1つ出す
  └─ それ以外                  → 通常 player_chat

次回発句
  → hard: 道具・読みの allowed/forbidden
  → soft: 蓄積 lessons（最大3・参考。全文 revision は載せない）
```

---

## 1. 直近句コンテキスト（セッション）

発句時に保持する（既存 `emitted_haiku` / emission を拡張）:

```text
RecentHaikuWorkshop:
  entry_id
  surface_text          # 詠んだ句
  kana_or_display       # 読み上げ形
  materials:            # irony/scene から
    irony_summary
    scene_motifs[]
    catalog_notes[]     # 短く
    biome_id / place
  emitted_at
  open: bool              # ワークショップ「積極モード」
  last_workshop_at        # 最後に句関連のやり取りをした時刻
  close_reason            # 閉じた理由（ログ用）
```

会話履歴（5往復）とは別。**句本文は pin なので履歴が押し出しても忘れない。**  
「いつ pin を外すか」は下のライフサイクルで決める。

---

## 1b. 句を忘れる／閉じるタイミング

状態は2段ある。

| 状態 | pin（句＋材料） | 入力の扱い |
|---|---|---|
| **open** | 毎回 details に載せる | 句関連意図を **優先**判定 |
| **closed** | **捨てる**（または短期キャッシュのみ） | 通常 player_chat。句の話は「さっきの句」想起が無い限り一般論 |

`closed` になったら **その句の workshop は終了**。  
長期の entry / critique / lesson は残る（「忘れた」＝会話のピンを外すだけ）。

### 閉じる条件（OR。先に満たした方）

| # | 条件 | 意図 | 例 |
|---|---|---|---|
| **C1** | **次の発句** | 新しい句が主役 | 次の「ここで一句」 |
| **C2** | **明示クローズ** | プレイヤーが区切る | 「もうええ」「次いこ」「わかった」「おk」「よし」 |
| **C3** | **肯定で完了** | 修正不要・満足 | 「いい句」「うまい」「そのままでいい」「気に入った」→ praise 保存のうえ close |
| **C4** | **直しの確定** | 改善が一段落 | `直し:` 成功、または自然文直しを保存した直後（任意で「まだ直す？」は出さず close） |
| **C5** | **話題の流れ（ソフト）** | 句を放置して別件へ | 下記 |
| **C6** | **時間切れ** | 放置 | 発句から **T_open**（案: 3〜5分）、または **最後の句関連から T_idle**（案: 90〜120秒）無入力の句関連 |
| **C7** | **セッション終了／切断** | 当然 | サーバ session 破棄 |
| **C8** | **緊急ゲーム状況** | 安全・優先 | panic / 死亡 等。close して脅威対応（句 pin は捨ててよい） |

### C5: 話題を流したとき（ソフトクローズ）

全部の雑談で即 close すると、「あの句さぁ」の一言目が消えるので、**二段**にする。

```text
open 中のプレイヤー入力
  ├─ 句関連（意味・講評・直し・ほめ・読み） → workshop。last_workshop_at 更新
  ├─ 明らかに別件（戦闘・移動・インベントリ・場所・無関係雑談）
  │     → 通常 chat で返事
  │     → 「流しカウント」+1
  │     → 連続 N 回（案: 2）または 流し後 T_drift（案: 60秒）で close
  └─ 曖昧 → 句関連寄りに1回だけ聞き返すか、chat へ（最初は chat でよい）
```

| パラメータ案 | 値 | 意味 |
|---|---|---|
| `N_drift` | 2 | 句と無関係な入力が連続したら close |
| `T_open` | 180〜300s | 発句からの最大 open 時間 |
| `T_idle` | 90〜120s | 句関連の最後から無活動で close |
| `T_drift` | 60s | 流し開始からの猶予（任意） |

**修正不要のとき:**

- 明示ほめ（C3）→ 即 close（lesson は praise 弱保存可）  
- 無言で歩き続ける → C6 時間切れ  
- 別の話を2回 → C5  
- 「いいね」相当がなくても **閉じることに問題はない**（entry は既に自動保存済み）

### 閉じたあとに句の話を再開したい場合

- 基本は **closed のまま**通常 chat（一般論になりうる）  
- 任意の後続:「さっきの句」「あの川柳」→ **entry を再 open**（短時間だけ）  
  - 実装は H3 以降の nicety。初版は「次の発句まで再 open なし」でも可  

### 忘れ方の原則

1. **会話用 pin は短命**（上の close）  
2. **長期記憶は長命**（entries / critiques / lessons）  
3. pin を履歴の長さに依存させない（5往復のまま）  
4. close 理由をログに残す（`close_reason=praise|drift|timeout|next_haiku|explicit|panic`）

### 状態遷移（要約）

```text
          発句
            │
            ▼
         [open]  ←── 「さっきの句」再open（任意）
        ／  │  ＼
   句関連  流し  時間/明示/ほめ/直し/次発句/panic
        ＼  │  ／
            ▼
         [closed]  pin 破棄
            │
            ▼
      長期 JSONL のみ残る
```

---

## 2. プレイヤー意図（自然文 → 種別）

形式コマンドは残しつつ、**自然文を分類**する。

| 種別 | シグナル例（粗い） | 動作 |
|---|---|---|
| **ask_meaning** | 「〜って何」「意味わからん」「ぐうの」 | 句の説明 or 正直に「読みにくい」 |
| **critique_forced** | 「無理やり」「詰め込み」「圧縮」 | critique kind=forced_compress |
| **critique_offscene** | 「ここ海ちゃう」「村なのに」 | kind=off_context |
| **critique_gibberish** | 「それ日本語？」「読めん」 | kind=unreadable |
| **praise** | 「いい句」「うまい」 | kind=praise（正例として弱く残す） |
| **revise_free** | 「こう直して」「〜の方がええ」＋句っぽい | revision 保存（`直し:` なしでも） |
| **revise_formal** | 既存 `直し:` | 現行どおり |
| **reading** | 既存 草地はくさち | 現行どおり |
| **close_workshop** | 「もうええ」「次いこ」 | open=false |

実装は最初 **ルール＋キーワード**で足りる。曖昧なものは  
`haiku_workshop` 用の短い LLM 分類（structured）に逃がしてもよい。

**重要:** 通常 chat に落とすと、今回のように一般論の俳句談義になる。  
`open` 中は workshop を優先。

---

## 3. 保存スキーマ（長期）

既存に足す（新ファイル案）:

### `haiku_critiques.jsonl`

```json
{
  "id": "...",
  "entry_id": "発句の id",
  "created_at": "壁時計 ISO",
  "kind": "unreadable|off_context|forced_compress|praise|other",
  "player_text": "原文",
  "normalized_note": "短い正規化メモ（システム生成可）",
  "materials_snapshot": { "motifs": [], "biome_id": "..." },
  "surface_at_time": "あさひさす …"
}
```

### `haiku_revisions.jsonl`（既存拡張）

- いまの formal 直しに加え、`source: "formal"|"conversational"`  
- 可能なら `critique_ids[]` を紐づけ  

### `haiku_lessons.jsonl`（または profile 内）

プレイヤー横断ではなく **ワールド／プロファイル単位**の教訓を薄く（**soft 既定**）:

```json
{
  "id": "...",
  "created_at": "...",
  "lesson_type": "readability|compress|scene|*",
  "note": "要素を少し絞って余白を残すとよい",
  "prefer_materials": true,
  "forbidden_fragments": [],
  "polarity": "tighten",
  "strength": 0.3,
  "from_entry_id": "...",
  "from_critique_id": "..."
}
```

**生成ルール（実装どおり）:**

| critique | lesson |
|---|---|
| unreadable / ask_meaning | `readability` soft: 読みやすさを少し意識… |
| forced_compress | `compress` soft: 要素を少し絞って… |
| off_context | `scene` soft: 材料・場面から大きく外れない方がよい |
| praise | **tighten を作らない**。`polarity: loosen` + `lesson_type: "*"` で既存を抑止 |
| other | lesson なし（critique 保存のみ） |

lesson の効き方（H5.1）:

- **soft 既定**（発句プロンプトは「参考。強制ではない」）  
- 最大 **2〜3 行**、`lesson_type` 軸は最新1件  
- `forbidden_fragments` は hard 禁止語に**合流しない**（道具・読みの forbidden は別途 hard）  
- `strength` は **記録のみ・当面未使用**（段階言い回しは予定しない。減衰は TTL）  
- **TTL（H5.2）:** 既定 14 日、または lesson 後の発句 6 回で list から消える  
- **明示緩め:** 「気にせんで」「注意いらん」等 → `loosen *`（workshop 外でも可）  
- プロンプト注入: `haiku_lessons_provider` → `_haiku_constraint_details.player_lessons`

---

## 4. 返事の型（workshop）

プレイヤーが直せる体験にするには、ただ「はい」では弱い。  
口答えは **ガチ約束せず soft**（lesson の強度と揃える）。

| 種別 | 返事の型（実装トーン） |
|---|---|
| ask_meaning | 「読みにくいかも」＋ **materials 開示** ＋「次は読みやすさ、ちょっと意識するわ」 |
| critique_forced | 「詰め込みすぎたかも」＋「次は余白、ちょっと意識するわ」 |
| critique_gibberish / offscene | 認める ＋ materials ＋「気をつける／外れすぎんように」 |
| revise_free | 「覚えといたで」＋ close |
| praise | 「残しとく」＋「**前の注意は少し緩めるわ**」（loosen と対応） |

**材料開示**が鍵。  
今回のログなら: irony/scene は良かったのに句が逸れた、とプレイヤーと共有できる。

直し案は（未実装・任意）:

- ルールベース短いテンプレ（materials の語を並べるだけ）  
- または `haiku` leaf を **repair モード**で1回（温度低）  

どちらも「プレイヤーが却下できる」前提。

---

## 5. 次回発句への効かせ方

```text
HaikuContext / 制約ブロックに追加（短く）:

使ってよい語: …（道具・読み hard）
使ってはいけない語: …（道具・読み hard のみ）

プレイヤーからの最近の癖・好み（参考。強制ではない。全文を写さない）:
- 要素を少し絞って余白を残すとよい
- 読みやすさを少し意識する（かな連続・謎語は控えめに）

【今回の材料（これが正）】
- motifs: 平原, 村, 朝, 銅のドア, オーク
```

- revision 全文・critique 全文は **載せない**  
- ベクトル RAG はまだ不要。lesson は JSONL 直引き  
- 読み訂正オーバーレイは現行のまま併用  
- hard 検証（`_respects_haiku_constraints`）は **forbidden_terms のみ**。player_lessons は見ない

---

## 6. 生成側の「自動直す」との関係

| やること | 優先 | 状態 |
|---|---|---|
| プレイヤーが直せる workshop | **本計画の主** | 済 H1–H5.1 |
| materials 固定語リスト突合 | — | **撤回**（生成が材料ベースなら冗長） |
| irony/scene は良いのに haiku だけ壊れる問題 | 生成改善 / workshop | 継続課題（リストではなく本流で） |

---

## 7. 実装 PR 分割案

| PR | 内容 | 依存 | 状態 |
|---|---|---|---|
| **H1** | 発句時 `RecentHaikuWorkshop`（materials スナップショット保持） | 既存 emission | **済** |
| **H2** | workshop 意図判定（ルール）+ open 中は chat より優先 | H1 | **済** |
| **H3** | `haiku_critiques.jsonl` 保存 + 材料開示つき返事 | H2 | **済** |
| **H4** | 自然文の直し → revision（`直し:` なし / `こう直して:` 等） | H3 | **済** |
| **H5** | lessons 生成・発句制約へ最大 3 行 soft 注入 | H3 | **済** |
| **H5.1** | ゆるめ・可逆（soft 文言 / hard 非合流 / praise loosen / 口答え soft） | H5 | **済** |
| **H5.2** | 明示「気にせんで」+ lesson 自然減衰（日数・発句回数 TTL） | H5.1 | **済** |
| **H6** | 発句後 materials 突合バリデータ（固定語リスト） | 独立可 | **撤回** |
| **H7** | （任意）workshop 分類の LLM structured | H2 の後 | 未 |

**H1〜H5.2 実装済み。H6 は撤回。**  
道具/読みの forbidden は hard のまま。player lesson は soft。  
**H6 をやめた理由:** 発句は渡した materials / scene から作る前提。固定 drift リストは本質でなくメンテだけ増える。  
「うみ」も場外れ断定は危うい（湖の圧縮・隣バイオームなどプレイヤー視点では自然なことがある）。  
場の違和感は **プレイヤーが言ったとき** workshop で。  
**strength 段階は当面やらない**（フィールドは残すが list 未参照。TTL で足りる）。  
**未（気が向いたら）:** 直し案 1 本、H7、Phase E 整理。

---

## 8. プレイヤー体験シナリオ（目標）

1. ドギド:「ここで一句。 あさひさす …」  
2. プレイヤー:「グーの木の水って何?」  
3. ドギド:「正直あれは読みにくいかもな。狙いは平原の村の朝と銅のドアやったんやけどな。直すなら言ってな。次は読みやすさ、ちょっと意識するわ」  
4. プレイヤー:「無理やり圧縮しすぎ」  
5. ドギド:「せやな、詰め込みすぎたかもな。次は余白、ちょっと意識するわ」→ critique + soft lesson  
6. プレイヤー:「こう直して: あさひさす / むらのどう / あかがね」  
7. revision 保存・pin close。次回発句で soft lesson が参考行として出る  
8. （後で）プレイヤー:「いい句やな」→「前の注意は少し緩めるわ」＋ loosen

---

## 9. やらないこと（この設計の範囲）

- 発句プロンプトに過去 revision を常時 few-shot で山積み  
- 履歴を長くして「いい感じに学習」だけに頼る  
- VLM を川柳の必須にする（建造物感想は別枠）  
- プレイヤーなしでの完全自動名句生成を目標にする  

---

## 10. 成功条件

1. 発句直後、自然な突っ込みが **workshop として保存**される  
2. 「何言ってるの」に **materials の正直な開示**が返る  
3. 講評が **soft lesson** になり、次回に **参考として短く**出る（常駐プロンプト肥大なし・hard にしない）  
4. `直し:` / 自然文直しでも revision に残せる  
5. praise で **loosen**（前の注意が弱まる）  
6. 既存の読み訂正・想起・自動保存・道具 hard 制約は壊さない  

---

## 11. 次の合意ポイント（残作業・ゆるく）

1. 直し案をシステムが1つ出すか（任意。現状は保存 + soft 返事。音数失敗時 repair は既存）  
2. H7 workshop LLM 分類（ルールで足りている間は不要）  
3. Phase E パッケージ整理（機能ではない）

合意後に実装。
