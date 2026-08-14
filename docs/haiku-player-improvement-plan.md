# 川柳: プレイヤー主導の改善設計

**日付:** 2026-08-12
**状態:** H1〜H5.2・H7-lite（OS／端末内AIの限定講評抽出）・修正案1本・連続局所編集 **実装済み** / H6 **撤回**（詳細は §7）
**関連:** [companion-maturity.md](companion-maturity.md)、[haiku-feedback-plan.md](haiku-feedback-plan.md)、[senryu-roadmap.md](senryu-roadmap.md)、[senryu-rag-plan.md](senryu-rag-plan.md)、[haiku-architecture.md](haiku-architecture.md)

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
  │     → 端末内AI優先で intent / 対象行 / 指摘を構造化
  │     → コード検証 → critique 保存 → 共同編集者として短く返す
  │     → プレイヤーの「〜に変えた方が」は、検証済み対象行だけへコードで置換
  │     → 置換語と三行をひらがな化・音数検査し、未保存の新しい三行をコード提示
  │     → 未保存案を基準に別の行も続けて置換できる
  │     → 求められたときだけ haiku route が対象行を直す
  │     → 別structured評価で意味保持・自然さを照合
  │     → コードで出典ID・重複・対象行・音数・発句時hard制約を検証 → 未保存案として提示
  │     → プレイヤーが「その案で」と明示して初めて revision 保存
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
  marked_line_index     # 次の明示置換を適用する一意な行。曖昧なら None
  pending_revision      # 未採用の最新三行。次の局所編集ではこちらを基準にする
  pending_revision_source
  current_revision_id   # 連続保存の親revision
  kana_or_display       # 読み上げ形
  materials:            # 発句シード（句テキストに制御タグは埋め込まない）
    interpretation      # irony/scene 要約
    motifs[]            # scene/irony focus 含む
    held_item / nearby_blocks / passive_mobs
    biome / biome_ja / structure / structure_ja / time_phase
    fragment_links[]    # surface(句) → material（かな部分一致の対応表）
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

- 明示ほめ（C3）→ critique 保存のうえ即 close。lesson は触らない
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
| **praise** | 「いい句」「うまい」 | kind=praise（critique 保存・lesson 非変更・close） |
| **request_repair** | 「直して」「直すかな」 | 検証済みの指摘から対象行だけ修正案を1本作る。自動保存しない |
| **player_line_edit** | 「夕暮れやに変えた方が」「中七を〜にして」 | finding／明示行で対象を固定し、その行だけプレイヤー語へ置換。未保存案を三行で提示 |
| **revise_free** | 「こう直して」＋完成した三行 | revision 保存（`直し:` なしでも） |
| **revise_formal** | 既存 `直し:` | 現行どおり |
| **reading** | 既存 草地はくさち | 現行どおり |
| **close_workshop** | 「もうええ」「次いこ」 | open=false |

明示操作と既知パターンは **ルール＋キーワード**が正。ルールで `soft_default` のとき、AI の intent は信頼度 0.75 以上なら **共同編集者の返答トーンだけ**に使う。lesson 保存・close・修正開始などの状態変更には使わない。既知 critique / `other_haiku` / `request_repair` ではAIが対象行・断片・問題種別を抽出するが、大分類を上書きしない。

finding は行 0〜2、閉じた problem enum、信頼度 0.65 以上をコードで検証する。行番号がなくても断片が一つの行だけに一致するときはコードで補える。`close`、`clear_lessons`、reading、revision 保存、open/close はAIに実行させない。特に、曖昧文をAIが `praise` と分類しても workshop は閉じない。

structured 抽出の `auto` 順は **Apple Foundation Models → Foundry Local → 既存 chat route**。設定と可用性だけで選び、生成内容で provider を選ばない。端末AIがない・失敗・低信頼・不正出力なら安全に fallback する。Apple はOSの現在の既定モデルを毎回取得し、Foundry は alias の解決先を定期確認する。Foundry の大容量モデル自動ダウンロードは既定 off。

`source=voice` の入力は、句・出典・時間帯など現在の候補内だけで一意な音近傍をコード補正する。STT原文は保持し、補正文はAIの講評理解と返答にだけ渡す。close、採用、lesson解除、revision保存などの明示操作は補正前の原文で判定する。

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

- `source: "player_feedback"|"formal"|"conversational"|"generated_confirmed"|"player_line_confirmed"`
- 連続局所編集は `base_text` と `parent_revision_id` を持ち、直前に採用した三行へだけ差分を適用する
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
| praise | critique 保存のみ。lesson は触らず、過去の指摘をキープ |
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

workshop の会話 leaf は、冒険時の「怖がり」ではなく **素直な共同編集者**。指摘を弁解せず受け止め、元の狙いを持ち出して句を守らない。実際に修正していない段階で「直した」「必ず直す」と約束しない。

| 種別 | 返事の型（実装トーン） |
|---|---|
| ask_meaning | 候補 materials をコードが閉じ、LLM が1つ選んで短く言う（「それは、平原やで」等）。失敗時は正直に読みにくい。**全 materials 羅列や schema 名は禁止** |
| critique_forced | 詰め込みを認め、直すべき点を短く返す |
| critique_gibberish / offscene | 読みにくさ／場のずれを具体的に認め、材料説明で反論しない |
| request_repair | haiku route が対象行だけ修正。コード検証を通った案だけ提示し、採用確認を待つ |
| player_line_edit | 置換語と完成三行はコード固定。LLMに本文を補作・復唱させず、別の行も続けて直せる |
| soft_default / other_haiku | 検証済み findings を渡した共同編集者 leaf。失敗時は短い定型 |
| revise_free | 「覚えといたで」＋ close |
| praise | 「ありがとうや。その句、残しとくで。」＋ critique 保存。lesson は触らない |

材料開示は `ask_meaning` で使う。講評への返事では、材料や狙いを弁明に使わない。

修正案は発話前ゲートとは別に、プレイヤーが明示的に求めたときだけ低温で1本生成する。検証済み finding の行だけを変更し、他の行は固定する。編集AIは全文ではなく、対象行ごとの `expected_text` と `replacement_text` を持つ差分を返す。コードが元行との完全一致・対象外行の不変・実際に字面が変わったことを先に確認し、生成AIの自己申告IDだけを信用せず、別structured評価で各修正行の意味保持・自然さを照合する。その後コードが、保存済み source atom ID、固定行との atom 重複、5-7-5±1、発句時にsnapshotした道具・読みhard制約を確認する。一回目が不合格なら、確定した失敗理由と不合格案を二回目の編集へ返し、同じ案は評価前に棄却する。二回とも不合格なら元句を維持する。合格でも `pending_revision` に置くだけで、句本文と採用案内はコードが固定し、前置きの一言だけ共同編集者 leaf が話す。疑問・否定を除く「その案で」等の明示採用時に、同じ差分が同じ元句へ適用できるか再確認し、検証済み行別出典・差分契約とともに revision 保存する。

プレイヤー自身が「〜に変えた方が」と語を示した場合は、生成AIを使わない。直前の検証済み finding、または「上五／中七／下五」の明示指定が一意なときだけ行を固定する。置換語はコードでUniDic読みへ展開し、カタカナもひらがなへ寄せる。残留漢字・英数・カタカナがあれば推測せず、ひらがな入力を求める。対象行は正確な5／7／5音、hard制約、他行重複を検査する。合格案は `player_line_compare_and_swap_v1` の未保存差分にし、さらに別の行を指摘された場合は、その未保存三行を表示上の基準にして差分を積み上げる。「全体はどうなった」への本文もコードが返す。最後に「その案で」と明示されたときだけ `player_line_confirmed` として保存し、採用句を次の編集基準へ昇格する。workshop は閉じない。プレイヤー語を source atom に偽装しないため、以後AI修正に必要な固定行出典が足りなければ、その経路はfail closedとする。

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
| **H1.1** | materials 厚み: motifs/held/nearby + 短い候補 + `fragment_links`（#28 phase 0–1） | H1 | **済** |
| **H2** | workshop 意図判定（ルール）+ open 中は chat より優先 | H1 | **済** |
| **H3** | `haiku_critiques.jsonl` 保存 + 材料開示つき返事 | H2 | **済** |
| **H4** | 自然文の直し → revision（`直し:` なし / `こう直して:` 等） | H3 | **済** |
| **H5** | lessons 生成・発句制約へ最大 3 行 soft 注入 | H3 | **済** |
| **H5.1** | ゆるめ・可逆（soft 文言 / hard 非合流 / praise lesson 非変更 / 明示 loosen / 口答え soft） | H5 | **済** |
| **H5.2** | 明示「気にせんで」+ lesson 自然減衰（日数・発句回数 TTL） | H5.1 | **済** |
| **H6** | 発句後 materials 突合バリデータ（固定語リスト） | 独立可 | **撤回** |
| **H7-lite** | OS／端末内AI優先の限定 structured 講評抽出。曖昧 intent 補助＋既知講評の行・断片・problem 抽出。コード検証・chat fallback | H2 の後 | **済** |
| **H7.1** | 要求時だけ大きい haiku route で対象行を修正。コード品質ゲート→未保存案→明示採用 | H7-lite | **済** |
| **H7.2** | finding／明示行を固定し、プレイヤー語をコードでひらがな化して連続局所編集。三行提示・CAS・採用後の次編集もコード所有 | H7-lite | **済** |

**H1〜H5.2 + H1.1 + H7-lite + H7.1 + H7.2 実装済み。H6 は撤回。**
道具/読みの forbidden は hard のまま。player lesson は soft。  
**H1.1:** 候補は短い名詞優先。`fragment_links` は句 surface→材料の内部対応表（句に制御タグを埋め込まない）。ask_meaning は links を優先。  
**H6 をやめた理由:** 発句は渡した materials / scene から作る前提。固定 drift リストは本質でなくメンテだけ増える。  
「うみ」も場外れ断定は危うい（湖の圧縮・隣バイオームなどプレイヤー視点では自然なことがある）。  
場の違和感は **プレイヤーが言ったとき** workshop で。  
**strength 段階は当面やらない**（フィールドは残すが list 未参照。TTL で足りる）。  
**H7-lite:** clear / close / revise / reading / hard off-topic はコード優先。Apple Foundation Models / Foundry Local を同じ契約へ接続済みで、利用不可・失敗時は chat route へ戻る。AI は状態や保存を直接変更しない。
**未（気が向いたら）:** OS AI／chat fallback の実ログ評価、Phase E 整理、#28 preface 延長・overlay。

全体の完成度・優先の考え方は [companion-maturity.md](companion-maturity.md)。

---

## 8. プレイヤー体験シナリオ（目標）

1. ドギド:「平原の村の朝と銅のドアの対比が頭に浮かんできたわ。ここで一句。」→「あさひさす …」  
   （見どころ〜本句のあいだは自分の世界＝プレイヤー雑談に乗らない）  
2. プレイヤー:「グーの木の水って何?」  
3. ドギド:「うん、その言葉は読みにくい。そこは直した方がええな」
4. プレイヤー:「無理やり圧縮しすぎ」  
5. ドギド:「せやな、詰め込みすぎた。余白を残すよう直した方がええな」→ critique + soft lesson
6. プレイヤー:「そこ直して」→ 対象行だけの案を提示（まだ保存しない）
7. プレイヤー:「上五は夕暮れやに変えた方が」→ コードがひらがな化・5音検査し、新しい三行を提示
8. プレイヤー:「下五は雨の夜に」→ 未保存の新三行を基準にもう一行だけ置換
9. プレイヤー:「その案で」→ revision 保存。採用句を現在句にしてpinを維持し、さらに別行も直せる
10. （後で）プレイヤー:「いい句やな」→「ありがとうや。その句、残しとくで。」＋ critique 保存（lesson は変更しない）

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
5. praise は critique 保存のみで **lesson を触らない**（過去の指摘をキープ）
6. 既存の読み訂正・想起・自動保存・道具 hard 制約は壊さない  
7. 対象行・断片・problem だけが閉じた型で抽出され、端末AIがなくても chat fallback で workshop が壊れない
8. 修正案は元行一致つきの行差分で、意味保持・自然さの別評価と、対象行・対象外不変・出典ID・重複・音数・発句時hard制約のコード検証を通る。不合格理由を再編集へ返し、同一案を再評価せず、明示採用までは元句と revision を変更しない
9. プレイヤーの局所置換は本文をLLMに生成させず、必ずひらがな三行として提示する。複数行を順に直しても、各差分の基準と親revisionがつながり、採用後もworkshopを続けられる

---

## 11. 次の合意ポイント（残作業・ゆるく）

1. OS／端末内AI・chat fallback の実ログ評価（誤分類、finding 精度、待ち時間）
2. 修正案を採用・却下するときの自然な言い回し追加
3. Phase E パッケージ整理（機能ではない）

H7-lite / H7.1 / H7.2 は実装済み。以後は実ログを見て、誤分類・待ち時間・提案の自然さだけを小さく調整する。
