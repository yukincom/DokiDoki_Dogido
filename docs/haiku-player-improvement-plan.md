# 川柳: プレイヤー主導の改善設計

**日付:** 2026-08-12
**状態:** H1〜H5.2・H7-lite・H9（検証付き共同編集エージェント。OS／端末内AIは戦闘中断中の小分類のみ）・修正案1本・連続局所編集・戦闘中断／再開 **実装済み** / H6 **撤回**（詳細は §7）
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

### 当初なかったもの（現行は実装済み）

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
  │     → 常駐会話モデルが現在句・未採用案・直近対話から相談目的と次の一手を選ぶ
  │     → 説明だけなら一手で返す。必要なら読み・音数・保存済み出典をコードへ確認
  │     → 検査結果を見て、説明／質問／修正提案／比較を一度だけ選び直す
  │     → 修正提案は既存haiku editorへ渡し、意味・自然さ・出典・重複・音数・hard制約を検証
  │     → 不合格理由も同じターンの次手へ返し、元句を保ったまま理由に沿って会話を続ける
  │     → プレイヤー自身の置換語は発話根拠・行概念・一意性・ひらがな・音数をコード検証
  │     → 合格案は未保存のまま提示し、現在pendingとCASを再確認した明示採用だけrevision保存
  │     → 実行したstep・検証結果・発話根拠を有界な改善記録へ残す（思考文・長期履歴は常時注入しない）
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
  open: bool              # pinのライフサイクル。combat_paused中もTrue
  combat_paused: bool     # 句とpendingを保持した一時中断。会話経路には載せない
  combat_paused_at
  combat_resume_pending_reason  # victory / defeated / escaped / safe
  combat_override_signature     # 動けない単独敵を明示無視した間だけ保持
  last_workshop_at        # 最後に句関連のやり取りをした時刻
  dialogue                # この一句だけの直近4往復
  agent_steps             # purpose / action / outcome / checks / validation code / evidence。最大12件
  last_repair_feedback    # 現在の元句に対する直近editor検証結果
  close_reason            # 閉じた理由（ログ用）
```

会話履歴（5往復）とは別。**句本文は pin なので履歴が押し出しても忘れない。**  
「いつ pin を外すか」は下のライフサイクルで決める。

---

## 1b. 句を忘れる／閉じるタイミング

状態は3段ある。

| 状態 | pin（句＋材料） | 入力の扱い |
|---|---|---|
| **open / active** | 毎回 details に載せる | 句関連意図を **優先**判定 |
| **open / combat_paused** | 本文・pendingを保持するが details には載せない | 戦況を優先。workshop用ASR補正・drift・timeoutを停止 |
| **closed** | **捨てる**（または短期キャッシュのみ） | 通常 player_chat。句の話は「さっきの句」想起が無い限り一般論 |

`closed` になったら **その句の workshop は終了**。  
長期の entry / critique / lesson は残る（「忘れた」＝会話のピンを外すだけ）。

### 閉じる条件（OR。先に満たした方）

| # | 条件 | 意図 | 例 |
|---|---|---|---|
| **C1** | **次の発句** | 新しい句が主役 | 次の「ここで一句」 |
| **C2** | **終了意図** | プレイヤーが区切る | 「もうええ」「お開き」「次へ行こう」「そのままでおしまい」「今日はここまで」等をagentが`close_workshop`へ抽出し、コードが根拠・否定等・pendingを検証してclose。利用不可時は旧`close_request`へfallback |
| **C3** | **肯定で完了** | 修正不要・満足 | 固定規則で明白な「いい句」「うまい」は praise 保存のうえ即close。規則外の自然な肯定評価は会話モデルが根拠つきで抽出し、コード固定の終了確認を一段はさむ |
| **C4** | **直しの確定** | 改善が一段落 | `直し:` 成功、または自然文直しを保存した直後（任意で「まだ直す？」は出さず close） |
| **C5** | **話題の流れ（ソフト）** | 句を放置して別件へ | 下記 |
| **C6** | **時間切れ** | 放置 | 発句から **T_open**（案: 3〜5分）、または **最後の句関連から T_idle**（案: 90〜120秒）無入力の句関連 |
| **C7** | **セッション終了／切断** | 当然 | サーバ session 破棄 |
| **C8** | **戦闘割り込み** | 安全・優先 | visual／auditory脅威・直近被弾で `combat_paused`。句と未採用案は保持し、戦況を優先 |

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

### 戦闘からの復帰

- プレイヤー撃破の根拠（論理サーバー死亡イベントの攻撃者、またはボス撃破確認）がある場合は「倒せたみたい」と戻す。
- 敵の死亡だけを観測し、プレイヤーへの帰属がない場合は「敵は倒れたみたい」と戻す。爆発死とクリーパー自身の爆散も別結果として扱う。
- 死亡観測なしの `combat_ended` は勝利と断定せず「離れられたみたい」と戻す。旧adapterだけは互換経路として近傍経験値オーブを使う。
- 戦闘終了／余韻のセリフがある間は復帰文を重ねず、次の静かな `normal` フレームまで待つ。
- 復帰時は保持していた最新版三行をコードから再掲し、「続ける？」と確認する。継続なら active、辞退なら close。
- 中断中の自然な発話はOS／端末内AI優先（失敗時は既存chat route）で `resume_workshop / workshop_input / close_workshop / unrelated / uncertain` の閉じた型へ分類し、confidenceと発話中の連続evidenceをコードで検証する。全AIが利用不可・低信頼・不正出力のときだけ「句を続けよう」「もうやめる」等の閉じた規則へfallbackする。AIはclose・保存・安全判定を実行せず、実際の状態変更はコードが行う。
- 敵がまだ見えていても、単独・3マスより遠い・非接近・聴覚脅威なし・直近被弾なし・同一個体が8秒以上安定し、かつOS AI／fallbackからプレイヤーの再開意思を確定できた場合だけ暫定復帰できる。再接近・被弾・敵数増加・個体変化で即pauseへ戻す。
- pause中は戦闘時間をopen／idle timeoutへ加算せず、意味説明待ち・終了確認待ち・一時的な対象行markだけを破棄する。現在句と未採用revisionは破棄しない。

**修正不要のとき:**

- 固定規則に一致する明示ほめ（C3）→ critique 保存のうえ即 close。lesson は触らない
- 固定規則外だが句全体への肯定評価と意味抽出できた場合 → praise critiqueを保存し、「ここまででよいか」をコード固定で確認。肯定後にclose
- 否定／賛否混在の評価 → openを維持。共同編集者leafへ「自分で完成句を作らず、どの行・言葉をどう直したいか一問だけ尋ねる」とコードから指示
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
4. close 理由をログに残す（`close_reason=praise|drift|timeout|next_haiku|explicit|meaning_confirmed|evaluation_confirmed|combat_interrupted_close`）

### 状態遷移（要約）

```text
          発句
            │
            ▼
         [open]  ←── 「さっきの句」再open（任意）
        ／  │  ＼
   句関連  流し  時間/明示/ほめ/直し/次発句
        ＼  │  ／
            ▼
         [closed]  pin 破棄 → 長期 JSONL のみ残る

 [open] --脅威--> [combat_paused] --戦闘終了--> [再開確認]
                      │                         ├─ 継続 → [open]
                      │                         └─ 辞退 → [closed]
                      └─ 安定した単独敵＋明示継続 → [open暫定]
                                      再接近／被弾 → [combat_paused]
```

---

## 2. 自然な相談から次の一手を選ぶ

形式コマンドと明白な短縮形は残すが、自然な相談は一つの `haiku_workshop_agent_step` が**会話全体から次の一手を選ぶ**。旧 `intent / evaluation / pending_decision` は、agentが利用不可・構造不正・低信頼・明示棄権になった場合の互換fallbackであり、通常進行の主ではない。

2026-09-05: 所持品質問の省略形を、有無・数量を表す文型に絞った。アイテム名と「ない」「何」が同じ文にあるだけでは判定しない。「黒い石は石炭じゃない？どういう意味？」は、先頭に「この句」を補わなくても、open中の句の文脈を使う意味抽出へ進む。「石炭ある？」「剣何本？」や明示の所持品確認は通常の会話経路を維持する。新しいAI判定やアイテム個別の例外は追加していない。

| 相談の例 | agentの代表的な一手 | コード境界 |
|---|---|---|
| 「この言葉って何」「狙いは？」 | `explain`、必要なら先に `inspect(source)` | 保存済み材料と直近対話だけを渡す。句・出典記録は変更しない |
| 「音数や読みを見て」 | `inspect(reading, meter)` → 結果を見て `explain / ask / propose_revision` | 読みと音数はコード測定。未検査の数値を発話させない |
| 「詰め込みすぎ」「ここ海ちゃう」 | `respond / ask / propose_revision` | 指摘の行・断片・problemを検証し、critiqueとsoft lessonへ写す |
| 「そこ直して」 | 対象が足りなければ `ask`、足りれば `propose_revision` | 既存editorの差分・意味・自然さ・出典・音数・hard制約を通す。自動保存しない |
| 「夕暮れやに変えて」 | `stage_player_edit` | 発話内の差し替え案・行概念・句中の対象箇所を検証し、対象行だけ未採用案へ差し替え |
| 「『さくらのは』を『さくらいろ』にするのはどう？」→「それにして」 | `respond / ask` → `stage_conversation_candidate` | 相談中の一案を対象箇所・差し替え表現・検査結果として保持。次の明示指示で再検査して未採用案へ移す |
| 「元と案を比べて」 | `compare` | 現在句と実在pendingがある場合だけ許可。採否は補わない |
| 「どんな句になった？」 | `show_current` | 本文はモデルに復唱させずコードから返す |
| 「この案を残して」「元へ戻して」 | `accept_pending / reject_pending` | confidence 0.85以上・音声認識原文にも行為を示す連続evidenceがあること・疑問／否定／条件／引用／伝聞でないこと・現在pending・CASを再検証して保存／破棄。同じ発話に明確な終了もあればclose evidenceの終了意思も検証して一つのtransactionにする |
| 「今日はここまで」 | `close_workshop` | confidence 0.85以上・今回発話根拠・pendingなしの場合だけコードがclose |
| 「こう直して」＋完成三行 / `直し:` / 明示reading / 明示praise | コードの速い経路 | 既存の決定的な保存・訂正・closeを維持 |

Rust経路では、会話中の一案を `pending` と別に保持する。保持するのは対象箇所、プレイヤーが示した差し替え表現、基準となる現在句・版、提示時の原文根拠、検査結果。音数不合格でも対象箇所まで忘れず、相談を続けられる。プレイヤー自身の言葉は返答生成後に保持し、ドギドの返答音声の中断や直近4往復からの退出では消さない。戦闘中は適用せず、同じ句の相談を再開したときだけ参照する。句の版変更、別の未採用案、相談終了で無効にする。ドギドの未再生の案は共有済みと扱わない。

「それにして」は保持中の一案を読み・音数・一意性・hard制約・CASで再検査して `pending` へ移すだけで、採用・保存はしない。「やっぱりXXにして」のように差し替え表現だけ訂正した場合は、今回の原文に新しい対象指定がないことを確認して同じ対象箇所を使う。新しい行・句中の箇所が原文に指定されていれば、モデルの抽出から抜けていてもその指定を優先する。`target_fragment` は句中の**対象箇所**、`replacement_text` はプレイヤー提示の**差し替え表現**。複数案の一覧・順位・選択機構は導入しない。

明確な引用付きの「『対象』を『表現』にするのはどう？」は、従来の文字列抽出を使って一案を先に確保し、そのターンのモデルには相談への返答を任せる。これ以外の自然文は既存の有界stepで抽出する。モデル呼出しの追加はない。実装・検証範囲は[一案の部分置換試験](evaluations/workshop-player-edit-20260928.md)を参照。

agent stepは `respond / explain / ask / inspect / propose_revision / compare / show_current / stage_player_edit / stage_conversation_candidate / accept_pending / reject_pending / close_workshop / unrelated` の一つだけを返す。通常stepはconfidence 0.72以上、状態変更候補は0.85以上かつ今回発話の連続evidenceを必須にする。会話理解には音声解釈を使えるが、局所編集・採否・終了の行為evidenceは今回の音声認識原文にも必要。新たに提示された差し替え案は提示時の原文で検証し、後で一時候補を選ぶときは今回の選択意思を検証する。採否と終了を同時に明示した発話だけは、採否actionに`close_after_action`と`close_evidence`を付け、採否と終了の意味をそれぞれコード検証する。同じ短い発話断片に両方が含まれる場合、二つのevidenceは重なってよい。説明・質問・比較だけが一文のspeechを返せる。句本文、未採用案、保存、終了、lessonをモデル自身が変更する余地はない。`unrelated`は同じ入力の通常雑談返答が成立した場合だけ、既存の二回driftへ参加させる。

一つのplayer turnは最大三つの計画stepに閉じる。典型形は `decide → inspect → after_inspection` または `decide → propose_revision → after_validation` である。`inspect` とeditor実行は各一回だけ。既存editor内の最大2回の再編集は維持するが、after-validation stepから同じeditorを再実行させない。schema不合格や棄権が最初に起きた場合は旧分類器へ戻し、実検査後に起きた場合は検査結果だけを使うコード固定返答へ戻す。

音声入力の同音異字は、文字列置換候補として先に確定しない。固定置換規則が候補を作っても、対象行・句中断片・直前markのいずれでも置換先を確定できず、会話モデルが根拠つき評価を返した場合は評価を優先する。実ログ固有の誤認識例はプロンプトへ追加せず、回帰テストにだけ残す。

agent stepが現行JSON契約を通っても、action許可範囲・発話根拠・confidence・finding・行概念・置換語・speechの外形をconsumerで再検証する。旧外形・キー欠落・型違いは共通clientが一回だけschema専用再試行し、なお不一致なら `schema_contract_error` として実行しない。旧 `haiku_workshop_intent / evaluation / pending_decision` の意味抽出とその検証は、agent fallbackとして残す。

agentの直接終了は、音声認識原文中で終了行為そのものを示す連続evidence、confidence 0.85以上、pendingなしを必要とする。疑問、否定、条件、引用・伝聞、現在句内の移動はコードで棄却する。未採用案がある場合は`close_workshop`を許可せず、`accept_pending / reject_pending`のevidenceと、同じ発話中の明確な終了を表す`close_evidence`をそれぞれ検証する。`accept_pending + close`は保存後、`reject_pending + close`は破棄後に閉じ、採否のない終了要求は旧pending fallbackのコード固定確認へ戻す。旧分類fallbackも同じ原文・行為evidence境界を通し、音声解釈だけから保存・破棄・closeしない。

`ask_meaning` の返答後は、コードが一時的に「意味説明済み」を保持する。次の発話はこの状態と一緒に会話モデルへ渡し、意味として納得・理解が得られた場合は `ack` として扱う。このターンでは新しいfindingを採らず、critique／lessonにも保存せず、コード固定で「この句の話はここまででよいか」を確認する。次の肯定で `meaning_confirmed` close、続行の意思ならopenへ戻す。単独の「そうなんだ」を常にcloseへ使うのではなく、説明直後だけの文脈依存操作とする。

プレイヤー自身の局所編集では、AI出力に `replacement_text`（プレイヤーが提示した差し替え案）、`target_fragment`（現在句中の対象箇所）、プレイヤー発話中の `evidence`、confidence を要求する。差し替え案と evidence が実際の発話に連続部分として存在し、対象箇所が現在句の一か所に確定した場合だけ、コードでその部分を差し替える。明示行指定があるときはその行に限って照合する。同じ行で対象箇所が重複すれば採らない。差し替え後の**完成した一行**をひらがな化・音数・hard制約・CASで検査し、未変更部分を維持する。表示表記の切れ目を安全に確定できない場合は、検証済みのひらがな読みを表示にも使う。AIが発話にない差し替え案を補作した場合は捨てる。

三行は本文文字列を二重管理せず、一行ごとに `line_id / line_index / position / canonical_name / surface_text / reading_text / source_atom_ids / source_atoms / provenance` を束ねる。漢字・カタカナを含む表示表記と確定ひらがな読みは同一句の二表現であり、TTS・音数・CASは読みを使う。プレイヤーの局所置換では、対象行の表示表記と読みを必ず同じ操作で更新し、未採用案から採用済み句への昇格・revision保存でも二つを分離しない。

三行には、言い方から独立した安定IDとして `line_1 / line_2 / line_3` を持たせる。人向けの概念番号は 1／2／3、コード配列の index は 0／1／2、位置概念は upper／middle／lower、現在の正規名は上五／中七／下五とする。会話モデルは「上の句」「中の句」「二の句」「最初の句」「真ん中」「後ろのパート」などをこの三概念のどれかへ写し、発話中の連続した `evidence` と confidence を返す。コードは既知の明示呼称、finding、target fragmentとの衝突や複数行への曖昧さを検査し、一行へ確定できない場合は編集しない。現在はプレイヤーの呼び方を訂正しない。抽出した生の呼び方・正規名・概念IDをログへ残し、将来のlearning版で自然な正規呼称を案内するためのフックにする。意味質問では行IDに対応づけた `source_atoms` を手がかりに、会話モデルが当時の材料・見どころ・直近対話を比較して説明する。対応自体が誤っていれば取り違えを認めるが、候補内という理由だけで無関係な由来を割り当てず、出典記録も自動で書き換えない。

未採用案がある間も同じagent stepが比較・質問・表示・採否を選ぶ。採用／却下はconfidence 0.85以上かつ音声認識原文にも行為を示すevidenceがある場合だけ候補とし、revision保存前に現在pendingと元句のCASをコードで再確認する。明示的な「その案で」等は従来どおりコードの速い経路、agent利用不可時は旧pending専用schemaと完全一致規則へ戻る。

finding は行 0〜2、閉じた problem enum、信頼度 0.65 以上をコードで検証する。行番号がなくても断片が一つの行だけに一致するときはコードで補える。agent actionや旧fallbackの`close_request`／pending採否をAIが抽出しても、`clear_lessons`、reading、revision保存、open/closeの実行はAIに渡さない。特に、曖昧文をAIが `praise` と分類しただけではworkshopを閉じない。

OS／端末内AIの `auto` 順 **Apple Foundation Models → Foundry Local → 既存 chat route** は、戦闘中断中の `resume_workshop / workshop_input / close_workshop / unrelated / uncertain` 分類にだけ使う。通常のagent stepと旧分類fallbackはOS AIへ問い合わせず、常駐する `chat` routeを使う。Apple はOSの現在の既定モデルを毎回取得し、Foundry はaliasの解決先を定期確認する。Foundryの大容量モデル自動ダウンロードは既定off。

`source=voice` の入力は、句・出典・時間帯など現在の候補内だけで一意な音近傍をコード補正する。STT原文は必ず保持し、補正文はAIの意味抽出と返答へ渡せる。lesson解除、明示reading、完成三行revisionは補正前の原文が正。自然な終了意図とpending採否はAIが意味を抽出するが、原文・補正文・evidence・confidenceをログに残し、終了や保存はコードのscope／pending／CAS検証後に限る。

**重要:** 通常 chat に落とすと、今回のように一般論の俳句談義になる。  
`open` 中は workshop を優先。

---

## 3. 保存スキーマ（長期）

現行の長期JSONL:

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

### `haiku_workshop_turns.jsonl`

固定規則・共同編集agent・旧分類器fallbackを含む、workshop入力の共通境界で自動記録する。何を考えたかではなく、**何を選び、コードが何を実行・検証したか**を残す。

2026-09-28からschema version 2を追記する。旧version 1の記録は変更しない。Pythonは設定済み記憶ルートの`long_term/haiku_workshop_turns.jsonl`、Rustは同ルートの`sessions/<session_id>/long_term/haiku_workshop_turns.jsonl`へ保存する。記憶保存を無効にした場合は記録しない。対象セッションのworkshopが特定できる入力を対象とし、接続先未確定のエラーを別の句へ結び付けない。

- `input_admission`: 入力受付、保留、受付拒否、音声を返さない即時処理。Rustの保留入力再配送は`forwarded_admission`で区別する。
- `decision`: Rustで検査・状態反映が済んだ時点。再生開始・成功を意味しない。
- `turn_result`: Pythonのイベント処理、Rustのworkshop入力jobの終了。成功・失敗・取消を含む。Rustは同じ`turn_id`の`decision`と対応させて、変更成功と音声失敗を分ける。
- `lifecycle`: 発句後の開始、戦闘中断／復帰、timeout・終了・接続切断など、句の状態変化。定期観測が同じ状態のままなら追記しない。

三行の正本・未採用案の前後、workshop ID、終了／中断状態、入力原文と解釈、処理経路・検査コードを残す。Rustは版番号・保持中のプレイヤー一案も記録する。`state_after`はその記録時点の状態であり、取消された古いturnが後続turnの変更を実行したという意味ではない。受付と結果は別のイベントなので、ファイルの行数を会話回数や成功回数として数えない。重複と判定されたイベント／turnは再記録しない。

Pythonは`result_scope=service_decision`・`playback_status=not_observed`で、音声の実再生を断言しない。Rustの`result.playback_status`は再生処理から得た完了／失敗／取消を保持する。記録のためのLLM呼出しは追加しない。Rustの追記は容量制限付きの別スレッドへ送り、正常終了時は書き込みを待つ。書き込み失敗や満杯は端末へ警告し、元の応答・句の採否を変えない。異常終了直前の未書き込み分の保存までは保証しない。

以下はversion 1から継承するstep部分の形式。version 2では上記の境界・状態・結果フィールドが加わる。

```json
{
  "schema_version": "1",
  "entry_id": "発句の id",
  "player_text": "音声認識原文",
  "semantic_player_text": "会話理解に使った解釈（補正なしなら原文と同じ）",
  "base_verse": "ターン開始時の正本",
  "pending_before": null,
  "pending_after": "未採用案または null",
  "steps": [
    {
      "phase": "decide|after_inspection|after_validation",
      "action": "inspect|propose_revision|ask|...",
      "purpose": "evaluate_verse|improve_wording|...",
      "outcome": "inspection_completed|proposed|rejected|...",
      "validation_codes": ["meaning_not_retained"],
      "checks": ["meter"],
      "evidence": "今回発話の連続部分",
      "close_after_action": false,
      "close_evidence": ""
    }
  ]
}
```

実行ループは一ターン最大3 step、永続化側も防御的に末尾6件、session内は直近12件に制限する。内部思考、agentのspeech、長い説明、不合格案全文、長期対話は保存しない。保存するのはターンの音声認識原文と会話理解用解釈・開始時正本・pending前後と、各stepの相談目的・action・outcome・checks・validation code・今回発話のevidenceである。このJSONLは監査・実ログ改善用であり、次回発句へfew-shot注入しない。accepted revisionは従来どおり`haiku_revisions.jsonl`、soft lessonは`haiku_lessons.jsonl`が正本である。

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

### 一句を直す間の文脈と再試行（2026-09-05）

`haiku/workshop_context.py` で、現在句・未採用案・発句時の見どころと材料・行別の照合先・直近4往復・前回の修正結果・直近のaction結果を、各stepへ共通して渡す。対話は既存の `DialogueContext` を使い、1発話320字、材料は最大24件、action結果は直近6件に制限する。現在行の照合先とその派生元を優先する。過去の句、長期評価ログ、長期記憶を新たに読み込む仕組みは追加しない。

### 検証付き共同編集ループ（2026-09-12）

`haiku/workshop_agent.py` は、現在句・未採用案・一句内の直近対話・今回発話・当該ターンの実観測を一つの閉じたstepへまとめる。モデルに長い思考文や自由なtool callは求めず、次の一手、相談目的、今回発話のevidence、必要なfinding／行提案、会話として返す一文だけを求める。

- `respond / explain / ask / compare` は一手で返せる。内部ID、未実行の修正・採用・保存、未確認の読み・音数・出典を発話した候補は棄却する
- `inspect` は `reading / meter / source` の必要項目だけをコードへ渡す。結果を次stepへ戻し、モデルは実測を見て説明・質問・修正提案を選び直す
- `propose_revision` は既存の行差分editorと全validatorを呼ぶ。合格時だけpendingへ置き、失敗時は閉じたvalidation codeを次stepへ戻す。同じturnでeditorは再度呼べない
- `stage_player_edit / accept_pending / reject_pending / close_workshop` はモデルの提案を直接実行せず、音声認識原文中の行為evidenceと置換語、confidence、疑問・否定・条件・引用・伝聞でないこと、行対象、現在pending、CAS、保存可否をコードで再検証する。採否と終了の同時指定は両意思をそれぞれ検証した一つのtransactionにする
- `unrelated` は同じ入力を通常雑談へ渡し、句pinは既存どおり二回連続driftで閉じる。通常雑談の返答をworkshop対話履歴へ混ぜない
- `show_current` の句本文、合格revisionの三行、採用案内、失敗時の正本維持はコード固定。初手のagent失敗は旧分類器、実観測後のagent失敗は観測に対応する固定返答へ戻す
- 戦闘割り込みと復帰確認、明示reading、完成三行revision、lesson解除、明白なpraise／close／pending採否は従来のコード経路を先に通る

- 対話には処理済み入力と返答として選んだ本文だけを残す。独立した知識質問・未処理入力・戦闘発話は混ぜない。同じ句の戦闘中断中は保持し、新しい句へは持ち越さない。
- 照合済みの対応も誤り得る判断として渡し、材料・詩的解釈・過去の説明を区別する。現在句と未採用案の出典も区別し、以前の同意を現在の採用・終了根拠にしない。
- 修正の再試行には、不合格案と失敗コードに加えて、照合モデルの具体的なコメントを最大240字で返す。コメントは事実や命令ではなく再考の参考。自動発句の最大6回、workshopの最大2回、差分照合と採用・保存条件は変更しない。
- 意味説明後でも、根拠つきの否定評価や修正相談と `ack` が競合すれば、納得として終了確認へ進めない。代表的な短い相槌の既存fallbackは維持する。
- **意味説明も実装・自動テスト済み。** 通常は共同編集agentが句・当時の材料・見どころ・直近対話を比較して`explain`を選び、出典確認が必要なら先に`inspect(source)`する。保存済み対応や前の説明の取り違えは認め、意味不明な表現に由来を作らず、プレイヤー語を自分の発句時の意図にしない。句本文・出典・未採用案は自動変更しない。agent初手不成立時だけ既存`haiku_workshop_reply`の`reply_goal=explain_meaning`へ戻り、生成失敗時も材料名を断定する旧固定文には戻さない。

共有文脈・agent step・検査後の再計画・旧fallback・原文再検証・既存機能との境界は自動テスト済み。ローカルQwenの独立stepでは、意味説明の一手、音数・読み・出典の`inspect → respond`、修正方向の質問、合成した意味保持／音数不合格後の再質問に加え、「その案で終わりにしよう」がschema再試行後に`accept_pending + close_after_action`としてconsumer検証を通った。実Minecraft／TTS、実editorを含む一連のE2E、長時間のaction選択品質は未確認。

### 返事の種類

workshop agentは、冒険時の「怖がり」ではなく **素直な共同編集者**。分類名に返事を直結せず、現在句・pending・直近対話から必要な一手を選ぶ。指摘を弁解せず受け止め、元の狙いを持ち出して句を守らない。実際に修正していない段階で「直した」「必ず直す」と約束しない。

| 種別 | 返事の型（実装トーン） |
|---|---|
| ask_meaning | agentが`explain`を選べる。一句の共有文脈を使い、材料との対応や前の説明が誤っていれば認める。出典確認が必要なら先に`inspect(source)`。記録は変更しない |
| ack_after_meaning | 説明を理解した相槌として受け、別の行を持ち出さず、コード固定の終了確認へ進む。納得自体はcritique／lessonにしない |
| critique_forced | 詰め込みを認め、直すべき点を短く返す |
| critique_gibberish / offscene | 読みにくさ／場のずれを具体的に認め、材料説明で反論しない |
| request_repair | haiku route が対象行だけ修正。コード検証を通った案だけ提示し、採用確認を待つ |
| player_line_edit | 置換語と完成三行はコード固定。LLMに本文を補作・復唱させない。TTSは変更後の三行だけを読み、直後に案内文を連結しない。pendingは維持するため、別の行も続けて直せる |
| soft_default / other_haiku | agentが`respond / explain / ask / inspect / propose_revision`から流れに合う一手を選ぶ。初手不成立時は旧共同編集者leafへ戻る |
| semantic evaluation | agentが評価を会話として受け、必要に応じて質問・検査・案作成を選ぶ。明白なpraiseと説明後ackの従来コード経路は維持 |
| revise_free | 「覚えといたで」＋ close |
| praise | 「ありがとうや。その句、残しとくで。」＋ critique 保存。lesson は触らない |

材料開示は `ask_meaning` で使う。講評への返事では、材料や狙いを弁明に使わない。

修正案は、agentが会話の流れから`propose_revision`を選び、コードが対象findingを検証できたときだけ、低温で1本生成する。検証済みfindingの行だけを変更し、他の行は固定する。編集AIは全文ではなく、対象行ごとの`expected_text`と`replacement_text`を持つ差分を返す。コードが元行との完全一致・対象外行の不変・実際に字面が変わったことを先に確認し、生成AIの自己申告IDだけを信用せず、別structured評価で各修正行の意味保持・自然さを照合する。その後コードが、保存済みsource atom ID、固定行とのatom重複、5-7-5±1、発句時にsnapshotした道具・読みhard制約を確認する。一回目が不合格なら、確定した失敗理由と不合格案を二回目の編集へ返し、同じ案は評価前に棄却する。二回とも不合格なら元句を維持し、validation codeをagentへ返して質問または説明を選び直させる。合格でも`pending_revision`に置くだけで、句本文と採用案内はコードが固定する。採用意図は同じagent step（利用不可時は旧pending schema）が閉じたactionへ抽出し、コードが同じ差分を同じ元句へ適用できるか再確認してから、検証済み行別出典・差分契約とともにrevision保存する。

プレイヤー自身が「〜に変えた方が」「〜でいいんじゃない」「〜にしてはどう」と語を示した場合は、まず会話モデルが置換語・evidence・句中のtarget fragmentに加え、言及された行を安定ID `line_1 / line_2 / line_3` へ写すが、句本文は生成しない。従来の閉じた文字列解析は、会話モデルが利用不可・低信頼・契約不合格だった場合のfallbackに限る。音声入力で「上五／中七／下五」が崩れても、「上の句」「二の句」「真ん中」「後ろのパート」等の位置表現を発話根拠つきで意味対応できる。また「くさちのねよりくさちかな」のように現在句の一行と新しい一行を同じ発話に含めた場合は、コードが旧句をUniDicで読みへ戻し、現在の三行に一意に一致する行を置換対象として固定する。直前の検証済み finding、明示行、検証済み行概念、または現在句の一行だけに一致するtarget fragmentで行を固定できる場合だけコードへ進む。複数行への一致や各根拠の衝突はfail closedとする。置換語はコードでUniDic読みへ展開し、カタカナもひらがなへ寄せる。残留漢字・英数・カタカナがあれば推測せず、ひらがな入力を求める。対象行は正確な5／7／5音、hard制約、他行重複を検査する。合格案は `player_line_compare_and_swap_v1` の未保存差分にし、さらに別の行を指摘された場合は、その未保存三行を表示上の基準にして差分を積み上げる。対象行の locate、会話モデルの行概念／proposal accepted・rejected、未保存案の staged・rejected は warning ログへ段階別に残す。「どんな句になった」「今の案を見せて」への本文もコードが返す。最後に採用意図を会話モデルが高信頼に抽出し、コードのpending・CAS再検証を通ったときだけ `player_line_confirmed` として保存し、採用句を次の編集基準へ昇格する。workshop は閉じない。プレイヤー語を source atom に偽装しないため、以後AI修正に必要な固定行出典が足りなければ、その経路はfail closedとする。

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
| **H7-lite** | 常駐する会話モデルの `chat` routeによる限定 structured 意味抽出。intent／findingに加え、句評価（極性・範囲・根拠）、行呼称→`line_1/2/3`、発話根拠つき一行置換、pending採否、自然な終了意図を閉じたschemaで抽出する。OS AIは通常workshopでは呼ばない。実行・保存はコード検証 | H2 の後 | **済** |
| **H7.1** | 要求時だけ大きい haiku route で対象行を修正。コード品質ゲート→未保存案→明示採用 | H7-lite | **済** |
| **H7.2** | finding／明示行を固定し、プレイヤー語をコードでひらがな化して連続局所編集。三行提示・CAS・採用後の次編集もコード所有 | H7-lite | **済** |
| **H8** | 戦闘時は句・pendingを保持してpause。OS／端末内AI優先＋chat fallbackで再開意思／句への具体的発話だけを限定抽出し、コード安全確認後に勝利／離脱を言い分けて再開。安定した単独敵も意思確認時だけ暫定再開 | H2 | **済** |
| **H9** | 自然な相談を一つの有界共同編集agentへ統合。現在句・pending・直近対話から次手を選び、必要時だけ読み／音数／出典を実検査。editor検証結果を一度だけ再考し、実行step・検証結果・発話根拠を有界な改善記録へ残す。状態・保存・CAS・戦闘はコード所有 | H7.1 / H7.2 | **済** |

**H1〜H5.2 + H1.1 + H7-lite + H7.1 + H7.2 + H8 + H9 実装済み。H6 は撤回。**
道具/読みの forbidden は hard のまま。player lesson は soft。  
**H1.1:** 候補は短い名詞優先。`fragment_links` は句 surface→材料の内部対応表（句に制御タグを埋め込まない）。ask_meaning は links を優先。  
**H6 をやめた理由:** 発句は渡した materials / scene から作る前提。固定 drift リストは本質でなくメンテだけ増える。  
「うみ」も場外れ断定は危うい（湖の圧縮・隣バイオームなどプレイヤー視点では自然なことがある）。  
場の違和感は **プレイヤーが言ったとき** workshop で。  
**strength 段階は当面やらない**（フィールドは残すが list 未参照。TTL で足りる）。  
**H9:** clear / 完成三行 revise / 明示 reading / hard off-topic と明白なpraise／close／pending採否はコード優先。それ以外の自然な相談は、常駐chatモデルが現在句・未採用案・直近対話・実検査結果から次手を一つ選ぶ。読み／音数／出典とeditor validationはコード実行、状態・保存・CASはコード検証。初手不成立時だけH7-liteの分類器群へ戻る。OS AI優先はH8の戦闘中断中小分類だけに残す。
**未（気が向いたら）:** 戦闘中断用OS AI／chat fallbackと、通常workshop agentの実ログ評価、Phase E整理、#28 preface延長・overlay。

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
7. プレイヤー:「最初の句は夕暮れやに変えた方が」→ 会話モデルが `line_1` へ対応させ、コードが根拠・ひらがな化・5音を検査して新しい三行を提示
8. プレイヤー:「後ろのパートは雨の夜に」→ 会話モデルが `line_3` へ対応させ、未保存の新三行を基準にもう一行だけ置換
9. プレイヤー:「よし、それで完成にしよう」→ 会話モデルが `accept_pending` を抽出し、コードのpending・CAS再検証後にrevision保存。採用句を現在句にしてpinを維持し、さらに別行も直せる
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
7. 自然な相談は一つのagent stepが現在句・pending・直近対話から次手を選び、対象行・断片・problem・プレイヤー置換語・pending採否・終了意図を必要なactionにだけ添える。「上の句」「二の句」「真ん中」「後ろのパート」等は`line_1/2/3`へ対応し、発話根拠・confidence・現在状態・他操作との衝突をコード検証する。会話モデルが使えない場合も旧分類器と代表的な明示規則fallbackでworkshopが壊れない
8. 修正案は元行一致つきの行差分で、意味保持・自然さの別評価と、対象行・対象外不変・出典ID・重複・音数・発句時hard制約のコード検証を通る。不合格理由を再編集へ返し、同一案を再評価せず、明示採用までは元句と revision を変更しない
9. 意味説明直後の納得は別の句断片への講評に化けず、保存なしで終了確認へ進み、肯定または続行をコードが確定する
10. プレイヤーの局所置換は本文をLLMに生成させず、必ずひらがな三行として提示する。複数行を順に直しても、各差分の基準と親revisionがつながり、採用後もworkshopを続けられる。現在は行の呼び方を訂正せず、抽出した呼び方と正規名を将来のlearning版用フックとして残す
11. 戦闘では句と未採用案を失わず会話だけ中断する。中断中発話の再開意思はOS AIの閉じた型＋根拠検証、敵の安全性と状態変更はコードに分ける。勝利／離脱後の静かなフレームで再開確認し、安定した単独敵を無視した後も再接近・被弾で即中断へ戻る
12. agentは必要時だけ読み・音数・出典をコード検査し、各検査結果またはeditor validationを返した後の再判断は一度だけ、全体を最大3 stepに閉じる。未実行の成功を話さず、改善記録へ実行step・検証結果・今回発話のevidenceだけを有界に残す

---

## 11. 次の合意ポイント（残作業・ゆるく）

1. 通常workshop agentと戦闘中断用OS AI／chat fallbackの実ログ評価（action選択、finding精度、待ち時間、検査後の返し）
2. agentによる採用・却下・追加修正の実ログ評価
3. Phase E パッケージ整理（機能ではない）

H7-lite / H7.1 / H7.2 / H8 / H9 は実装済み。以後は実ログを見て、action選択・待ち時間・提案の自然さだけを小さく調整する。
