# 川柳 workshop 取り込みパターン調査

**日付:** 2026-08-12
**状態:** 調査・比較（H1〜H5.2・H7-lite 反映済み）。**観察ログの正本は Issue #8**
**Issue:** [#8 観察台帳](https://github.com/yukincom/DokiDoki_Dogido/issues/8) ← **実例はここにどんどん追加**  
**関連:** [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md)

方針の前提:

- **回数を取る前に解を決めない**（W0 = Issue に観察を貯める）  
- **明示操作・hard off-topic・状態管理はコード（SM / pure function）**。H7-lite は `soft_default` の intent 補助と、既知講評を含む対象行・断片・problem 抽出だけを行う
- open 中は hard off-topic だけ chat に戻し、それ以外のマーカー外発話を `soft_default` として扱う
- この docs は比較メモ。**追記の本丸は Issue コメント／本文**

---

## 1. 現状パイプライン（要約）

```text
player-input
  → reading_correction?  → 保存返事
  → formal/conversational 直し? → revision
  → その他 haiku 操作?
  → classify_workshop_intent (open 時のみ有効な経路)
       対象 kind（soft_default / other / request_repair / 各 critique）
                    → OS／端末内AI優先の限定 structured 抽出
       既知 intent  → ルールの大分類を維持し、対象行・断片・問題種別だけ抽出
                    → soft_default のintentは返答トーンだけに利用
                    → 低信頼・失敗はルール結果／chat fallback
       hard off-topic → 通常 player_chat
                        → speech あり → workshop drift
```

いま intent に載るもの（マーカー）:

| kind | 例 |
|---|---|
| close | もうええ、おけ |
| praise | いい句やな |
| critique_forced | 詰め込み、圧縮 |
| critique_gibberish | 読めん、意味わからん、グー |
| critique_offscene | 場違い、海ちゃう |
| ask_meaning | 〜って何、意味 |
| other_haiku | 句・川柳・詠ん を含む |
| revise | 直し: / こう直して: |

**載らない**自然文（実ログ）:

- 腹っぱの間違いかな  
- じゃあ平原にハラッパっていう  
- 項目を足した方がいいかもしれないね（メタ・句外）  

---

## 2. プレイヤー発話パターン台帳

実装前にログ／実プレイから行を足していく。  
`gate` = open 中に「句の話」とみなすべきか（人間ラベル）。

| ID | 例文 | 意図（人間） | 現状 | 望ましい経路 | gate |
|---|---|---|---|---|---|
| P01 | グーの木の水って何? | 意味質問 | ask_meaning ✅ | workshop + 材料開示 | YES |
| P02 | 読めん / 意味わからん | 読みにくい | gibberish ✅ | workshop | YES |
| P03 | 無理やり圧縮しすぎ | 詰め込み | forced ✅ | workshop + lesson | YES |
| P04 | いい句やな | ほめ | praise ✅ | workshop + critique 保存 + close（lesson 非変更） | YES |
| P05 | 直し: あ / い / う | 直し | revision ✅ | revision + close | YES |
| P06 | こう直して: … | 自然文直し | conversational ✅ | revision | YES |
| P07 | 腹っぱの間違いかな | 読み・語句の疑い | critique_gibberish ✅ | workshop soft / 読み疑い | YES |
| P08 | はらばって何 | 語の意味 | ask_meaning ✅（「って何」） | workshop | YES |
| P09 | じゃあ平原にハラッパっていう | 読み教え | soft_default → H7-lite ⚠（読み保存にはならない） | **reading_correction** | YES |
| P10 | 草地はくさち | 読み教え | reading ✅ | reading | YES/外でも |
| P11 | そうちじゃなくてくさち | 読み訂正 | reading ✅ | reading | YES |
| P12 | 海ちゃうやろ | 場違い | offscene ✅ | workshop | YES |
| P13 | この句どう思う? | 句への言及 | other_haiku ✅（「句」） | workshop | YES |
| P14 | 項目を足した方がいいかも | メタ・システム | soft_default → H7-lite ⚠ | **chat**（drift 可） | NO |
| P15 | 松明ある？ | ゲーム雑談 | chat | chat + drift | NO |
| P16 | おはよう | 雑談 | chat | chat + drift | NO |
| P17 | （句の一部）はらば おかしくない？ | 断片＋疑い | critique_gibberish ✅ | workshop | YES |
| P18 | さびたもってランタン？ | 材料確認 | soft_default → H7-lite | workshop ask_meaning 寄り | YES |

**収集ルール:** 実プレイで「ピンが立ってるのに chat に落ちた／落ちて正解だった」発話を P19… に追記。  
週に数行でよい。

---

## 3. 失敗モード（なぜダメか）

| 失敗 | 原因 | 体感 |
|---|---|---|
| F1 intent 漏れ | マーカー網が狭い | 句の話なのに chat |
| F2 読み訂正漏れ | 「AにBっていう」未対応 | 教えが保存されない |
| F3 drift 誤爆 | intent None の speech を無関係扱い | pin が消える |
| F4 topic 過適合 | open 中も identify（イカ・ウマ） | 句と無関係な返事 |
| F5 マーカー全域拡大 | open 外でも workshop | 雑談が吸われる |
| F6 LLM に振り分け | 判定をプロンプトに | 不安定・方針違反 |

---

## 4. 解決アーキテクチャ案（多角比較）

### 案 A — マーカー増やすだけ

intent に「間違」「おかしい」「っていう」等を追加。

| 長所 | 短所 |
|---|---|
| 実装が最小 | F5 リスク、網羅が終わらない |
| | 読み訂正は別問題のまま |

**単独では不十分。**

---

### 案 B — open 中「句関連ゲート」二段（推奨の芯）

```text
if not workshop.open:
    既存のみ
else:
    if is_about_pinned_haiku(text, workshop):  # コード
        kind = classify_workshop_intent(text) or "other_haiku"
        # 読み訂正は別途先に試す
        workshop path; drift しない
    else:
        chat; drift 可
```

`is_about_pinned_haiku` の材料（優先度イメージ）:

1. 既存 intent ≠ None  
2. 読み訂正パターンにマッチ  
3. 誤り語: 間違 / 誤って / おかしく / ちゃうやろ（句文脈）  
4. pin 表面の **かな連続 3+ 文字** が text に含まれる  
5. 句 / 川柳 / この句 / 詠  

| 長所 | 短所 |
|---|---|
| open 限定で安全 | ゲート設計が肝 |
| F1+F3 を同時に潰せる | 断片マッチの false positive 要テスト |
| 判定がコード | — |

**本命の骨格。**

---

### 案 C — 読み訂正パターン拡張（B と並行必須）

`extract_reading_correction` に:

- `平原にハラッパっていう` / `〜に〜って（言う|よぶ）`  
- open 中は surface が漢字でなくても、**材料・biome ラベルと対応**すれば採用（慎重に）  
- open 中の読み成功時は **drift しない**（既に reading は drift 除外だが、経路に乗ることが前提）

| 長所 | 短所 |
|---|---|
| ログ P09 を直接直す | 口語ゆれが多い |
| 次の発句に効く | 過検知（日常の「っていう」）→ open 中優先で緩和 |

---

### 案 D — open 中は「全部 other_haiku」

open 中は intent 無視で全部 workshop。

| 長所 | 短所 |
|---|---|
| 実装が単純 | P14–P16 も吸う。松明質問が句の話になる |
| | やりすぎ |

**不採用寄り。** ゲートなし全域はダメ。

---

### 案 E — open 中 chat は topic 抑制のみ

intent は狭いまま、chat の stance を none 寄りに。

| 長所 | 短所 |
|---|---|
| イカ誤爆は減る | 読み訂正・critique 保存がされない |
| | pin の価値が半減 |

**補助策。** 単独では不足。

---

### 案 F — LLM structured で intent 分類（H7-lite、限定採用）

| 長所 | 短所 |
|---|---|
| 自然文に強い | 遅延・不安定さがある |
| ルールの抜けを講評種別へ寄せられる | 低信頼・失敗時の fallback が必要 |

**限定採用済み。** ルールで `soft_default` になった場合も、閉じた enum と信頼度ゲートを通した intent は返答トーンだけに使い、lesson・close・repair開始へは使わない。既知 critique / other / repair では対象行・断片・problem の抽出だけに使う。明示操作・hard off-topic・状態管理はコードのまま。

---

## 5. 推奨組み合わせ

```text
B（open 中 soft 既定）+ E（hard off-topic は chat）+ F（限定 intent / findings 抽出）
A は intent 精度向上として部分採用
C は読み保存の未解決パターンに限定して継続検討、D は不採用
```

### 処理順（open 中）

```text
1. reading_correction → 保存・返事・drift しない
2. revise / formal 直し
3. classify_workshop_intent
     既知 intent    → workshop 返事・critique・drift しない
     句関連         → H7-lite（OS AI優先、失敗はchat、状態判断はコードのまま）
     hard off-topic → chat（drift 可）
```

### テスト観点（パターン駆動）

各 P0x について:

- intent / gate の期待  
- 経路（workshop / reading / chat）  
- drift 有無  
- 副作用（松明が workshop に入らない）  

---

## 6. 実装フェーズ（案）

| フェーズ | 内容 | 対応パターン |
|---|---|---|
| **W0** | 台帳に実ログを追加 | 継続 |
| **W1** | open 中 soft 既定 + hard off-topic の chat/drift | **済**。P07, P13, F1, F3 |
| **W2** | 読み「AにBっていう」等 | P09 は未、P10 は済 |
| **W3** | pin 断片マッチ + 誤り語 | **済**。P17, P07 |
| **W4** | open 中 topic 誤爆の抑止 | 継続観察。F4, P14 |
| **W5** | 限定 structured intent / findings 抽出 | **H7-lite 済**。実ログ評価は継続 |

---

## 7. 成功条件

1. P07 / P09 が workshop または reading に入り、**drift しない**  
2. P15 / P16 は chat のまま、誤って workshop に吸われない  
3. open 中にイカ・ウマ identify が起きない（または大幅減）  
4. 新パターンは台帳に足してからマーカーを増やす（場当たり禁止）  

---

## 8. 次のアクション

1. **Issue #8 に観察を足す**（本文 or コメント。テンプレは Issue 先頭）  
2. OS AI／chat fallback の低信頼・誤分類・finding 精度を実ログで確認する
3. P09 の読み保存と P14 のメタ発話は、実害を見てコード側マーカーを調整する

---

## 変更履歴

| 日付 | 内容 |
|---|---|
| 2026-07-19 | 初版。パターン台帳・案 A–F 比較・推奨 B+C+E |
| 2026-07-19 | 観察の正本を Issue #8 に。回数不足のうちは実装しない |
| 2026-08-12 | H7-lite を OS／端末内AI優先の限定 intent / findings 抽出へ更新。残る P09 / P14 を明記 |
