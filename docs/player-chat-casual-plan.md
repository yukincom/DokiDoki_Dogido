# player_chat 雑談3本柱 — 実装計画

**日付:** 2026-07-16  
**状態:** **P1〜P5 + 現在ターンの予定／安全方針 + 有界grounded planner 実装済み**

**関連:** [player-chat-topic-overfit-plan.md](player-chat-topic-overfit-plan.md)、[player-chat-sm-vs-prompt.md](player-chat-sm-vs-prompt.md)

---

## 2026-10-01 Rust本体の共通対話

キャラクターモードは `base / normal / tension / workshop`。平時は `normal` を使う。
共通設定の `base / normal / tension` は35字以内、川柳相談は120字以内。

通常会話と自発的な環境反応は、同じ相棒の対話として扱う。人格は
`dogido_server/llm/companion_prompts.json` のBaseを共通参照し、場面の調子、
観測の扱い、今回の目的、出力形式を分ける。川柳・共同編集・国語対話も同じBaseを参照する。
分類器や検証器には相棒の人格を混ぜない。生成用の静的データには共通人格のコピーを持たせない。

`dogido-rust/src/chat_prompt/data.json` は、場面ごとの目的・観測や履歴の扱い・材料の配置を持つ。
性格、方言、怖がり方、返事の長さは共通プロンプトへ任せ、会話用データで重ねて指定しない。
旧Pythonの生成元・比較oracleは退役した。現在はこのRust側データを編集し、対応する検証fixtureと期待する契約を揃える。`chat_prompt/fixtures.json` は完成プロンプトの検証用写しで、
通常起動では読み込まない。共通プロンプトと会話用データはRustのビルド時に取り込む。
発言／沈黙の返答は、説明やコード囲みを付けないJSON一件とする。出力形式の指示にもコード囲みの実例を含めない。

共通バトルトーンは廃止した。即時の戦闘警告はコードが決める。
個別のLLM反応は `llm/reaction_situations.json` の `event / situation` と、現在の観測・
一般的性質・共通の対話履歴を渡す。状況文は人が直接編集する正本で、Rustのビルド時に取り込む。
Rust本体は現在の対象IDと反応対象から正本カタログを読み、対象の説明文・全特徴タグ・比喩や役割・場所の説明を、観測とは別の `catalog_knowledge` として通常会話と自発反応へ渡す。元の文言やタグを要約・先頭数件で切り捨てず、モブの職業・子供の差分も現在観測に対応するものだけを使う。ポータルはコマンド用品カタログにあるブロック説明も読む。スカルクやウォーデンの音反応は音から確定した対象の一般説明であり、視認や召喚成功の根拠にはしない。一般的なポータル説明より、現在地から確定した役割・行き先を優先する。場面や感情のタグは会話の材料であり、必須の演技指示ではない。現在の対象と明示的に名前の出た種類を読むだけで、全カタログの会話への投入や新たな在否推定は行わない。
モブの水・雨・日光・暑さ・次元などによる性質と条件付きの変化は、正本カタログの`environment_notes`へ置き、既存の特徴・比喩と一緒に渡す。Java 1.21.11を対象にし、水中生物の陸上生存、雨による乾燥防止／被害、アンデッドの燃焼・水没変身、粉雪・次元・落雷による変化を一般知識として保持する。現在の水・雨への接触、頭部水没、接地、燃焼はFabricが個体ごとに実測し、通常会話と自発反応の共通`observations.mob_states`へ種別カタログIDと個体IDを結んで渡す。プレイヤー自身の接水も別に渡す。同種の別個体を混ぜず、未知は未知のまま、見えなくなった個体の未消化の変化も消す。継続時間や変身・死亡の確定を一般知識から補わない。日中の水中コメントは`daylight_water`の共通反応とし、スケルトン以外も対象種の知識と実測を渡す。旧leaf名は互換入力として受け付ける。日光燃焼対象はカタログの`burns_in_daylight`を参照する。

村人が現在の視認観測・視線先・視認人数に含まれる間だけ、ゲーム内時刻と年齢・職業から解決した `villager_routines` を通常会話と自発反応へ渡す。仕事・集会・遊び・自由時間・休息の切替は共通文脈の変更履歴にも残し、範囲外になれば現在の日課と未消化の変更を取り除く。日課は実際の睡眠や移動を測定した結果と分けて表示する。時刻・年齢が未取得なら未確認とし、仕事の時間帯の職業不明も自由時間と決めない。夜の室内でも受信済み時計があれば日課を解決する。Java 1.21.11のtimelineに合わせ、子供の遊びは3000–5999／10000–11999 tick、0–9 tickは前日の休息を維持する。ネザー／エンドでは現行アダプターが時計を送らないため未確認。実個体の睡眠状態はこの変更では追加しない。
村ではFabricが4件への切り詰め前に集計した、16ブロック以内で見通しが通る村人数を渡し、10人以上を「多い」とする。村全体の人口ではなく、未確認・範囲内未検出も区別する。
ポータルは直前のロード済み同一座標の変化と前方の見通しを確認した `appeared`（目の前に出現）、範囲へ入った `arrived`（ある場所に到達）で状況文とfallbackを分ける。新規出現でも前方の見通しを確認できない `observed`、区分が欠けた観測では中立文を使う。現在地とポータル種別をコードで照合し、起動済みの入口・帰還用の出口・エンド内移動の通路という役割も共通の状況文データから渡す。未対応の組み合わせや現在地不明では移動先を未確認とする。
スカルクセンサー／シュリーカーは実際の発動音だけを専用反応にし、停止・設置・破壊などは通常の音観測へ残す。発動原因者や音源の視認を音だけから決めない。ディープダークの状況文は、作動音・叫び声・心音・その他のウォーデン音を聞いたという最小限の事実にする。振動への反応、条件付きの召喚、音だけでは戦闘中と分からないことは、対象カタログの `behavior_notes` に置く。シュリーカーの召喚条件は[公式の説明](https://www.minecraft.net/en-us/article/sculk-shrieker)、ウォーデンの感覚と探索は[公式の紹介](https://www.minecraft.net/en-us/article/meet-warden)に合わせる。既存の説明・比喩を残し、音だけで召喚成功や発見済みへ昇格させない。音の優先順はウォーデン音＞心音＞シュリーカー＞センサー。Fabricは4秒の既存観測保持中に下位音で上位音を上書きせず、下位音で保持期限も延ばさない。Rustはセンサー発動後の1秒間は生成を保留し、その間のシュリーカーへ切り替える。生成待ち・生成中・再生中でも新しい上位音が来れば下位の専用反応だけを取り消し、上位への切替は共有クールダウンを越えて通す。同順位・下位は既存のクールダウンを維持する。ソニックの即時悲鳴と通常の戦闘警告・プレイヤーの質問は別の優先経路を保ち、ソニックを予兆LLMでも重ねて生成しない。天候変化と実雷鳴・落雷は別の状況文を使い、本体の環境反応でも同じ正本を読む。悲鳴音声は同じ配送の実再生完了後だけ「悲鳴を上げた直後」と渡し、再生予定・悲鳴なしを区別する。
「必死に喜ぶ」「岸へ来いと願う」などの個別演技指示、例文、語彙・感情による採否は使わない。
発言と沈黙は共通対話プロンプトに任せ、観測に反する撃破や照明の入手方法の断言は従来どおり検査する。
通常会話も脅威や戦闘後というだけで狼狽を要求しない。戦闘・panic・aftermathの
キャラクターモードは追加トーンを持たない `base` とし、安全ヒントは現在の脅威の根拠から渡す。

通常の返事にも、現在の場所・天候・明るさ・解決済みの匂いと、前回の判断以降の変化を渡す。
会話待ちの間に更新があれば、生成を始める直前に観測と履歴を取り直す。その判断に渡した版だけを
処理済みにし、生成中に到着した新しい変化は次の判断へ残す。観測、一般知識、player報告、
過去の発話を分け、匂いの方角を目視位置・距離・個数へ変換しない。

返事に雨や匂いへの反応を織り交ぜてもよい。通常会話・環境反応・個別のLLM反応で、明示的な
`action=silent` を選べる。沈黙では読み補正・TTS・再生を起動しない。
空出力、不正なJSON、打切り、生成失敗、再生失敗・取消を沈黙へ読み替えない。
通常会話の不合格は従来の一度の言い直し、環境反応は従来の一回生成とfallbackを保つ。

選んだ沈黙は `role=event, reaction=silent` の「黙って受け止めた（発話なし）」として
短期履歴へ残す。通常会話の次のplannerと発話生成、環境反応のどちらからも読める。
実際に再生を終えた環境発話も共通履歴へ入れる。沈黙をassistantのセリフ、再生済みの一往復、
会話由来の川柳材料へ数えない。戦闘優先、workshop、操作・保存、明示照会のコード判断は維持する。

コード・自動テスト・模擬LLM/TTSによる通信試験で確認済み。
実モデルでの自然さ、実Minecraft・実音声は未確認。旧Python本体の本文形式と互換分岐は運用終了。現行の生成契約はRustの `speak/silent`。

## 継続する設計原則と旧実装の記録

以下の設計原則はRustでも維持する。日付・PR・Pythonファイル名・試験件数は移植前の経緯として残し、その実装手順は終了した。現在の対応先は `dogido-rust/src/planner/`・`chat_observation.rs`・`chat_validation.rs`・`chat_prompt/`。起動・検証は [Rust本体](../dogido-rust/README.md) を参照する。

## ゴール（3本柱）

| # | 柱 | 一言 |
|---|---|---|
| **1** | **none を守る** | 弱い語・ASRゆらぎで偽モブ identify に引きずらない |
| **2** | **本当の観測だけ短く** | visual / passive / hearing / ついさっき を短い事実として渡す |
| **3** | **5往復＋LLM で相槌** | 履歴は伸ばさない。雑談のリズムは LLM＋既存履歴 |

成功イメージ:

| 入力 | 期待 |
|---|---|
| もしもし / マイク直った / すいません | stance=**none**、自然な相槌。偽種名レールに入らない |
| 大きい木／気があるね | stance=**none**（または clarify）。シロクマ骨子なし |
| なんだあのババア | stance=**hypothesis**、ウィッチ可 |
| 変な旗 | hypothesis、ピリ可 |
| 川にサケがいる状況でサケの話 | 観測ラベル「サケ」が事実に載る。弾かれない |
| 前哨基地ある？（タイガ） | 現在構造物IDと一致すれば確認済み。バイオームやピリジャー視認だけでは在ると言わない |
| よしよし、大丈夫そうですね | 会話の続き。`丈夫そう` をラバのvisual tagへ流さない |
| 近くにラバがいるの？（観測なし） | 「いない」とは断定せず、現在観測では未確認と返す |

---

## 現状の問題（コード上）

| 場所 | 問題 |
|---|---|
| `resolve_reply_stance` | `if topic_hits: return hypothesis` → **1 ヒットで identify レール** |
| `find_catalog_topics` | 「大きい」「きれい」など **GENERIC タグ単独**で hit |
| narration details | hypothesis でなくても **topic hints を載せうる** → モデルが引っ張られる |
| `build_identify_skeleton` | hypothesis なら骨子。弱い二択（シロクマかスニッファー）も出る |
| 観測のプロンプト載せ | threat / hearing はある。**passive は allowed には入るが「見た事実」1行が弱い** |
| 履歴 | 5往復のまま維持（変更しない） |

---

## 柱1: none を守る

### 1.1 stance 入場条件（T-A）— 最優先

**現行実装:** `dogido-rust/src/planner/`・`chat_topics.rs`。旧Pythonの `player_chat_policy.py` / `resolve_reply_stance` は退役。

**変更後ロジック（案）:**

```text
1. has_visual / threat に「視認」 → saw
2. topic_hits を「identify 用に使える hit」にフィルタ（1.2）
   usable_hits が空 → （3へ）
3. usable_hits があり、かつ次のいずれか → hypothesis
   a. identify 意図（何/誰/あいつ/あれ 等。既存 _CLARIFY_HINTS を整理）
   b. 高信頼: 非 GENERIC 語を含む match、または score 閾値＋ top 独走
   c. 観測一致: hit.entry_id ∈ visual∪passive∪hearing 解決 id
4. identify っぽいが usable_hits 空 → clarify
5. それ以外 → none
```

**ポイント:**  
「topic が1件でもある」だけでは hypothesis にしない。  
「大きい木があるね」は usable_hits 空 or 意図なし → **none**。

### 1.2 GENERIC タグ単独ヒットを落とす（T-B）

**現行実装:** `dogido-rust/src/chat_catalog.rs`・`chat_topics.rs`・`planner/`。以下は旧Python時代の実装順の記録。

```text
GENERIC_TOPIC_TERMS = frozenset({
  "大きい", "小さい", "きれい", "白い", "黒い", "赤い", "青い",
  "長い", "短い", "丸い", "速い", "怖い", "変な", "強い", "弱い",
  # 必要なら実測で追加。旗・ババア・とんがり帽子・前哨基地は入れない
})
```

ルール:

- hit の `matched_terms` が **すべて GENERIC** → identify 用 hit から除外  
- matched に **1つでも非 GENERIC**（旗、ババア、前哨基地…）→ 残す  
- `find_catalog_topics` 自体は描写用に残してもよいが、**player_chat 経路ではフィルタ後だけ使う**

### 1.3 hints / 骨子 / 白リスト enforce を stance に連動（T-C + 既存）

**ファイル:** `narration._render_player_chat_reply`

| stance | catalog_topic_hints | identify_skeleton | speech_whitelist_enforce | plausibility |
|---|---|---|---|---|
| **none** | **載せない** | なし | True（許可名が空なら種名を生成させない） | 載せない※ |
| **clarify** | 載せない | なし | True | 載せない |
| **hypothesis** | identify actionの usable_hits のみ | 高信頼時のみ | True | identify actionでstructure語があるとき |
| **saw** | 載せない（観測優先） | なし（視認優先） | True | 載せない |

※「前哨基地ある？」は usable 非 GENERIC → hypothesis なので F′ は残る。

**S3 骨子追加条件:**

- matched に非 GENERIC がある  
- 同点トップ2がどちらも GENERIC 由来なら骨子禁止  
- 骨子は LLM の生成材料に限る。LLM 無効・生成失敗・usable 不合格時の
  fallback 本文には流用せず、話題非依存の中立文へ戻す
- 2文字以下の短いカタカナ名は、かな折り畳み部分一致を使わない
  （例: 「いいんじゃないかな」内の「いか」を「イカ」と誤認しない）

### 1.4 テスト（柱1）

| ケース | 期待 stance | 期待しないもの |
|---|---|---|
| 大きい気があるね | none | シロクマ、スニッファー骨子、hints |
| 大きい木があるね | none | 同上 |
| きれいな〜 | none | 熱帯魚 hints |
| なんだあのババア | hypothesis | — |
| 変な旗持ってる | hypothesis | — |
| もしもし | none | 種名レール |
| あれ何？（特徴なし） | clarify | 種名骨子 |

---

## 柱2: 本当の観測だけ短く

### 2.1 観測ソース（既にあるもの＋整理）

| ソース | 現状 | 計画 |
|---|---|---|
| visual_threats | threat_summary | 維持 |
| recent_visual_memos | ついさっき視認した記憶。現在の在否照合には含めない | 済 |
| passive_mobs | 今フレームの生き物を短いobservation行と現在の照合IDに載せる | 済 |
| recent_passive | 過去の視認として経過秒と「現在の在否は未確認」を付記。現在の照合IDには含めない | 済 |
| hearing + buffer | 音・在否の問いに音メモを渡す。現在の照合IDは今フレームの音源だけ、bufferは「ついさっき」 | 済 |
| 標高・気候・積雪 | 現在Y＋気温＋降雪開始高度をコード比較し、閉じた気象事実だけを公開。地表雪は周辺の雪ブロック実測 | **川柳と共通判定で済** |
| topic（弱い） | hints に載りうる | **none では載せない**（柱1） |
| topic（強い） | hints | hypothesis のみ |

### 2.2 `observation_summary` 1ブロック（新規 details）

**組み立て（narration）:** 優先順で最大 **4行**。

```text
観測メモ（短い事実。無い行は出さない）:
- 視認: ピリジャー 前 12マス
- ついさっき: ウィッチ 右
- 近くの生き物: サケ、ウシ
- 過去の視認（現在の在否は未確認）: ヤギ（58秒前）
- 音: ゾンビっぽい 左 far
```

ルール:

- **今フレーム or バッファ retention 内だけ**  
- 過去の視認・音は時制を保った会話材料に限定し、現在もそこにいる根拠にしない
- 種名はカタログ label 解決できたものだけ（hearing と同じ思想）  
- トピック仮説の種は **ここに入れない**（観測と仮説を混ぜない）  
- 重複（視認とついさっきが同じ種）は1行にまとめてよい  
- global の `rain` は現在Yとバイオーム気温で雨／雪へ解決する。降っている雪と、
  実ブロックで確認した地表の積雪は分け、未観測の積雪を断定しない
- Y・Z 座標、気温、降雪開始高度、`downfall` は LLM に渡さない。`downfall` を
  降水確率として解釈させず、現在の降水・雷・降雪環境・実測積雪だけを渡す

**プロンプト (`player_chat_prompts`):**

- `threat_summary` / 分散していた事実を **`observation_summary` に集約**してもよい（段階的で可）  
- 最低限: threat を残しつつ、**passive 1行を追加**  
  `近くの生き物: サケ`（passive があるときだけ）

### 2.3 allowed_speech_labels と観測の一致

- allowed = **コード観測由来 ∪ 現在／過去のplayer発話に実在するカタログ名 ∪（plannerが同定を選んだときだけ usable topic labels）**
- 名前を会話に使う許可と現在の存在確認は別。保持期間内の過去の観測名も「さっき見た」会話には使えるが、現在の照合IDへ戻さない
- 全通常雑談で enforce を有効にする。assistant履歴だけに現れる種名は許可へ入れず、過去の誤断言を新しい世界根拠にしない
- player発話の種名は、その人の報告として会話を続けるために許可するが、観測済みへは昇格しない
- 動物園: 観測があれば載る → habitat 不要  
- 観測した種に限り、カタログの `observed_speech_aliases` を allowed に加える。現在は商人のラマ→ラマ、洞窟スパイダー→スパイダー、ヒカリイカ→イカ
- 危険な一般化は `observed_speech_rewrite_from_ids` を持つ種だけ、直近10秒の視認・聴取・討伐推定を根拠に白リスト判定前へ戻す。現在は村人ゾンビを「ゾンビ」とした場合だけ「村人ゾンビ」へ修正
- 一般種そのものも同時に観測済み、または同じ一般名に複数の修正先がある場合は曖昧なので修正しない。ガーディアン／エルダーガーディアンのような併存は両方を allowed にし、プレイヤーに合わせる言い方を LLM に任せる

### 2.4 digest（任意・小さめ）

- 既存 `event_digest`（〜8件）はそのまま5往復と併用  
- 変更するなら: ambient「サケを見た」が確実に digest に入っているか確認（既に `〜を見た` あり）  
- **reject / style_mismatch の文は dogido 履歴に載せない**。一回再考へ渡す候補も一時的な同一生成内の文脈だけで、再生完了履歴には載せない

### 2.5 テスト（柱2）

| 状況 | details に載る | 載らない |
|---|---|---|
| passive に salmon | 近くの生き物: サケ | シロクマ |
| visual 0・バッファ pillager | ついさっき 視認 ピリ… | — |
| 大きい木・観測なし | 観測ほぼ空 | topic シロクマ |
| ババア・観測なし | topic ヒントのみ（hypothesis） | 偽の視認行 |

---

## 柱3: 5往復＋LLM で相槌

### 3.1 変えないもの

| 項目 | 値 |
|---|---|
| `DialogueContext.max_utterances` | **10（5往復）のまま** |
| max_digest_notes | 8 のまま（必要なら後で） |
| 履歴の全文検索 RAG | **やらない** |

### 3.2 プロンプト（none 時の形）

```text
参考傾向:
- 相棒の返事。実況・定型あいさつにしない
- 直近の会話の流れに沿って自然に返す。会話を続けるためだけの質問を足さない
- 【答え方】雑談として自然に。根拠のない種名捏造はしない
- （戦闘時のみ静止禁止など）

本番:
【直近の会話】…最大5往復…
【直近の出来事メモ】…あれば…
プレイヤー:「…」
場所メモ: …
時間帯: …
答え方スタンス: none
観測メモ: …あれば短く…
（topic hints なし）
→ 12〜42字で一言
```

### 3.3 LLM がやりやすい条件（実装チェックリスト）

- [x] none で **偽 topic / 偽骨子が details に無い**
- [x] 観測があるときだけ **短い事実行**がある
- [x] none でも、観測またはplayer発話にある種名だけを許可し、assistant履歴だけの種名は捨てる
- [x] 会話継続それ自体を目的にせず、確認・相談が必要なときだけ質問する。完了や相槌には問い返しを足さない
- [x] 自然な自己言及・謝罪・方言・比喩は広域禁止語で落とさず、空出力・役割ラベル・英語説明・生成崩れと具体的なgrounding／安全違反だけを検査する
- [x] 不合格時は候補と閉じた理由を同じ会話へ一度返し、意味と人格を残した言い直しを最大1回だけ生成・再検査する。再不合格時だけ、topic hit や identify 骨子を本文にしない**話題非依存の中立 fallback**へ戻す
  - 任意改善: none + unusable のときだけ、もう少し相槌寄りの fallback  
  - 例: 「おう、聞こえてるで」は以前問題になったので使わない。  
    「うん」「そうやな、もうちょい Tra 言って」程度の中立相槌を別キーにしてもよい  

### 3.4 履歴に載せないもの

2026-09-05: 既存の5往復を使うプロンプトに、発言者と返答先を確かめ、訂正・異議・困惑には直前の説明と現在の観測を比較して答え直す方針を追加した。自分の過去の推測を新たな観測事実にせず、分からない点は認める。履歴件数、生成回数、会話以外の操作条件は変更していない。文脈の受け渡しは自動テスト済み、実際の会話品質は実機確認待ち。

| 載せない | 理由 |
|---|---|
| style_mismatch / unusable で捨てた LLM 生文 | サケ蒸し返しループ |
| identify 誤骨子がプレイヤーに出なかった場合 | 同上 |
| cue 擬音（既存） | 維持 |

**実装確認:** player発話は受理時に追加するが、Dogido本文は選択・表示時点では追加しない。`AudioDispatcher` が発話IDつき `completed` を返し、次の直列game eventで回収した本文だけを `add_dogido` する。`failed / cancelled / queue replaced` は履歴と会話由来の川柳材料へ入れない。

---

## データ流（変更後）

```text
user_text
  → 現在ターンの明示予定（return_home のみ。保存しない）
  → 現在フレームの安全方針（地表夕方 or 雷雨。保存しない）
  → completed済み5往復 + 現在入力 + コード観測
  → player_chat_plan（閉じたaction + 発話内evidence + confidence）
  → actionが対象照合のときだけ find_catalog_topics (raw)
  → filter_usable_topic_hits (GENERIC 除去) + 観測ID照合
  → resolve_reply_stance (saw / hypothesis? / clarify / none)
  → observation_summary (visual+buffer+passive+hearing のみ)
  → allowed = 観測 ∪ player発話の実名 ∪ planner同定候補
  → enforce_wl = 全通常雑談
  → hints / skeleton / plausibility = identify + hypothesis 時のみ（条件付き）
  → 未観測の在否・過去誤断言の訂正はコード固定文
  → details → LLM
  → usable sanitize
  → 直近観測に基づく一意な危険一般名の修正
  → style / allowed_speech_labels sanitize
  → 不合格時だけ候補 + コード理由を同じ会話へ返して一回言い直し
  → usable / grounding / style を再検査（再不合格なら固定fallback）
  → player入力を受理
  → 発話IDつき本文を選択・音声queueへ
  → completed された文だけ履歴5往復へ
```

## 有界grounded planner（2026-09-11）

通常 `player_chat` は本文生成の前に、structured kind `player_chat_plan` で一度だけ会話焦点を決める。これは汎用ReActや世界操作toolではなく、次のread-only actionから一件を選ぶ限定plannerである。

- `continue_conversation`
- `check_entity_presence`
- `identify_entity`
- `answer_observation`
- `clarify_reference`
- `correct_previous_reply`

plannerは発話を生成せず、状態変更・保存・assistを実行しない。`action / focus / entity_query / evidence / confidence` を共通structured contractへ通し、evidenceは現在入力を必須にして、直近の実再生済み会話の連続部分だけを許可する。明示在否問い、playerの平叙存在報告、所持品問、音の問いはコード側のrouting hintでactionも検査し、問いを単な相槌へ落としたり、報告を観測照合へ昇格したりさせない。失敗・低信頼・不正JSONは保守的なコードfallbackへ戻る。

談話関係を先に解くため、通常の相槌や評価文はカタログ全件の描写タグ検索へ流さない。対象照合actionだけがカタログを読み、その候補IDを今フレームのvisual / passive / hearing、および現在構造物 / 乗車中の乗り物 / 視線先entityのコード観測IDへ照合する。候補順位は先に発話だけで決め、関連Mobの視認で別対象へすり替えない。カタログ一致は観測ではない。未観測の在否質問は「現在観測では確認できない」、過去のassistant誤断言は謝罪と現在観測の切り分けをコード固定で返し、「いない」までは断定しない。

2026-09-17: 平和・中立Mobの60秒の視認記憶を「近くの生き物」へ混ぜていた箇所を分離。過去の視認は経過秒付きの会話材料にだけ残し、敵の視認・音の短期bufferも現在の在否照合から外した。「ヤギなんぞ見えんぞ」のような名前付きの視認否定、直前の単一対象への短い不在指摘は、既存plannerの在否照合hintで検査する。引用・条件・部分の見え方・複数対象からコードで対象を決めない。未確認なら固定の観測回答、今も検出されている場合はプレイヤーの画面で見えることと区別する。隠れた・逃げた等の理由を補作せず、追加のAI呼び出しはない。ローカルQwen3.6-35B-A3Bの6場面のテキスト試験で、58秒後の在否問い・名指しの否定・省略した否定の3場面は未確認回答となった。実マイク・Minecraft・TTSは未確認。

5往復、緊急反応、正本DB、assist、workshop、保存条件は変更しない。2026-09-11の全体回帰は **1314 passed、1 skipped、1500 subtests passed**。実Minecraft・実LLM・実TTSでの自然さと遅延は未確認。

## 発話候補の一回再考（2026-09-11）

`player_chat_plan` の後に作る本文だけを対象に、小さな observe → revise → revalidate を追加した。最初の候補が検査に通れば追加呼び出しはない。不合格時だけ、前の候補をassistant発話、コード由来の一件の理由を次のuser観察として同じプロンプト末尾へ足す。二案目を同じコード検査へ通し、最大2生成で終了する。

採否・fallback・世界事実・状態変更はコードのまま。不合格理由は閉じた種類だけで、モデルに次の行動や保存を選ばせない。このため汎用ReActではなく、通常雑談一文の有界な会話修正である。`repair_requested / repair_accepted / repair_rejected / repair_failed` のログで実会話の効果と追加遅延を比較する。不合格案は再生されず、completed履歴にも入らない。

ローカルQwenの独立テキスト試験では、崩れた匂い質問への初案 `playerはどう？` が `non_japanese_explanation` を受け、日本語だけの二案目へ直った。意図的な `なんか花の匂いがするで。` は `unsupported_olfactory_claim` を受け、非嗅覚の相槌へ一回で戻った。`ドギド:` の無害な話者ラベルは再生成せずcleanだけで外れた。モデル読込後の追加生成は各約0.7秒だった。全Python回帰は **1346 passed、1 skipped、1510 subtests passed**。これは実Minecraft・実TTSや長時間の自然さを証明しない。

### 通常雑談の家らしさ（2026-09-11）

通常雑談へ渡す場所投影では、設定済みリスポーン地点から既存の `home_bed_prompt_distance` 以内にいて、周辺にベッドまたはドアが観測された場合を `home_base` とする。これは所有権や完全な安全を断定せず、「家・拠点らしい場所」という会話上の表現だけを強める。

- 暗さ、低い天井、囲まれ度、洞窟バイオームより `home_base` を先にする。ただし水中と、直近のブロック破壊で確認した採掘中はその現在行動を優先する
- ベッドだけ、ドアだけ、リスポーン地点だけでは足りず、近いリスポーン地点との組み合わせを必須にする
- 窓は家の根拠にしない。窓だけで `home_base` へ昇格させない
- この変更は通常雑談の場所表現だけであり、暗所危険度、洞窟検出、緊急シェルター、安全判定は変更しない

## foreground会話と自動川柳（2026-09-09）

- `casual` の友好・中立Mobのambientは、最後のplayer入力から30秒間止める（`DOGIDO_CONVERSATION_AMBIENT_MUTE_MS`）。会話保持の5分期限とは分離し、途中の再入力で待ち時間を数え直す。敵対警告・雷・夕方注意は止めない
- hostile時は一件だけ話題を保留し、明示再開されなければ戦闘後の受理済みplayer turn 10件で破棄する
- 雑談が無期限に続いても自動川柳は通常10分周期で保留せず、現在のplayer replyの後ろ、または次の安全なqueue境界で割り込む
- 2026-09-17更新: 雑談中も生成した情景描写を先に話す。固定導入への置換は撤回。情景音声と本句生成は `haiku_preparation`、生成検査を通った句の完成後だけ `haiku_workshop` として期限を開始する
- 再生完了済み直近3 turnを、最大80字・最大3 motif・元turn IDつきの `player_reported_context` soft材料にする。プレイヤー発話由来であり世界の実測事実ではない
- `learning / web` 中は発句周期そのものを凍結する

詳細は [本体の会話所有権・中断・再生確定](main-dialogue-integration.md)。

## 現在ターンの予定と安全方針（2026-08-15）

- `time_phase` と `safety_priority` は別フィールド。`夕方なので帰宅` のような結合文として保存しない
- `safety_priority=seek_safe_place` は、夜警告と共有する地表判定が有効で、現在が夕方または雷雨のときだけ毎フレーム導出する
- 洞窟バイオーム・水中・空が見えない場所・安全な屋内では安全方針を載せない。朝昼や雷雨終了では次フレームから自然に `none` へ戻る
- `player_turn_plan=return_home` は「帰らなくちゃ」「拠点に戻ろう」等、現在発話に根拠があるときだけ。会話メモリや JSONL へ保存しない
- 帰宅予定のターンでは無関係な passive 観測を `observation_summary` から外し、明示予定を優先する
- `respawn_distance` は短い窓の複数サンプルが同方向へ動いたときだけ `approaching / leaving`。単発差分は `unknown`
- 帰宅・避難方針と衝突する追加の遠出提案は style 不合格として、安全な固定 fallback へ戻す
- 「洞窟探検」「家を作る」等の短期目標スロットはまだ設けない。行動ログから目標を推測しない

---

## PR 分割（実装順）

| PR | 柱 | 内容 | 主なファイル |
|---|---|---|---|
| **P1** | 1 | GENERIC フィルタ + stance 入場条件 | `player_chat_policy.py`, tests |
| **P2** | 1 | none/clarify で hints・骨子・plausibility を載せない | `narration.py`, prompts はほぼそのまま |
| **P3** | 2 | `observation_summary`（passive 行含む）を details + プロンプト | `narration.py`, `player_chat_prompts.py` |
| **P4** | 1+2 | 回帰テスト一式（木／ババア／旗／サケ観測／もしもし） | tests |
| **P5** | 3 | topic 非依存の中立 fallback、履歴汚染の確認 | fallbacks, service |
| **P6** | 1+2+3 | 会話焦点を先に解く有界plannerと対象のコード照合 | planner, narration, contracts, tests |
| **P7** | 3 | 広域禁止語の緩和 + 不合格理由を返す一回再考 + 同じ検査で再採否 | llm client, sanitize, prompts, tests |

推奨: **P1 → P2 → P4 の一部 → P3 → P4 完了**。  
P1 だけで「大きい気→シロクマ」は止まる。

---

## 受け入れ（プロダクト）

1. 森で「大きい木／気」→ **シロクマ／スニッファー骨子が出ない**、stance=none  
2. ババア・旗・前哨 → **今どおり identify / plausibility**  
3. サケが近くにいる → 事実 or allowed にサケ。雑談でサケに触れて **style で落ちない**  
4. もしもし等 → **none + 相槌**。ようわからん連打にならない（P5 まで含むとより良い）  
5. 履歴は **5往復のまま**
6. 「大丈夫そう」等の会話継続 → **描写タグから種名を補わない**
7. 在否問い → **カタログ一致だけで存在断言せず、現在観測と照合する**

---

## 明示的にやらないこと

- 会話履歴の延長（10往復化など）  
- 履歴ベクトル検索  
- 全 mob の spawn_biomes  
- 今すぐ VLM（将来枠は overfit 計画に記載済み）  
- ASR 大規模修正  

---

## 実装状況

| PR | 状態 |
|---|---|
| P1 GENERIC + stance | ✅ |
| P2 none で hints/骨子/plausibility 非載荷 | ✅ |
| P3 observation_summary | ✅ |
| P4 回帰テスト `tests/test_player_chat_casual.py` | ✅ |
| P5 topic 非依存の中立 fallback | ✅ |
| P6 有界grounded planner + 対象のコード照合 | ✅（コード・自動テスト。実モデル未確認） |

### 旧Pythonでの変更ファイル（退役済みの記録）

- `dogido_server/player_chat_policy.py` … usable filter / stance  
- `dogido_server/state_machine/mixins/narration.py` … 経路分岐 + 観測サマリ  
- `dogido_server/llm/player_chat_prompts.py` … observation 節  
- `dogido_server/dialogue/player_chat_planner.py` … 会話焦点と一件のread action、対象照合
