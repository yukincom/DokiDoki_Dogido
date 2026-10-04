# 川柳アーキテクチャ

**現行実装: Rust本体（2026-10-03）。** 発句・検査は `dogido-rust/src/haiku/`・`python_worker.rs`、共同編集は `workshop_*` と `dialogue/workshop_runtime.rs` が所有する。旧Python本体と比較oracleの運用は終了。発句方式・根拠・採否の仕様は継続し、起動は [Rust本体](../dogido-rust/README.md) を参照する。

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
6. `chat` route で `IronyContext` を抽出する。`description` は自然な関西弁の見どころ文として、その時点で音声キューへ送る
7. 次のフレームで scene JSONを使って発話済みの見どころを一次 atom IDへ結び直す。一節だけの応答と複数節の応答を同じドメイン検査へ通し、コードで既知 atom ID・主張区分・長さ・重複を検査して、`preface_clause` と句全体で共有する `poetic_interpretation` atom を作る。scene が拾い損ねた場合も、見どころの明示要素を二文字以上の意味のある一致で一次 atom へ戻す
8. 設定で固定した生成方式を添え、`haiku` route（初稿 temperature `0.60`）で、かな三行の JSON を生成する
9. `chat` route（temperature `0.0`）で、一行ごとに「どの atom の意味が残ったか」「自然な日本語か」を JSON 判定する。検証済み `poetic_interpretation` がある場合は、材料名の逐語的な再現ではなく、句が発話済みの情景に反せず、そこから自然に立ち上がる比喩・余情・印象かを判定する
10. 漢字混じりの候補は、利用可能なら既存UniDicで意味・語順を変えず読みへ展開し、かな化後の同じ行を検査へ戻す。辞書無し・未知語・漢字残留は推測せず従来どおり不合格にする
11. コードで、かな・音数・道具 hard 制約・出典 ID・一次 atom 単位の行間重複を検証する。句全体の意味の枠である `poetic_interpretation` だけは三行で共有できる
12. 不合格行を含む**生成スロット**だけを `haiku` route（temperature `0.30`）で再生成する。各対象行へコードで確定した失敗理由・現在音数・目標音数・許容範囲を渡す。実測中は最大 6 回（設定上限 8 回）
13. 空白・句読点・カナ種だけが違う既出候補はコードで即時棄却し、意味評価を再実行しない
14. 合格三行を一行オブジェクトへ確定する。各行は安定ID・位置概念・表示表記・確定ひらがな読み・出典atomを一緒に持ち、TTS・音数・workshop CAS は確定読みを使う。漢字・カタカナを含む表示表記とひらがな読みを別の句として扱わない
15. 6 回後も一行でも不合格なら、句全体を発話せず `まとまらんかった` 系 fallback に閉じる。失敗句は workshop pin・長期保存の対象にしない

irony の `description` は発句前に実際に話す短い詩的解釈であり、scene はその発話を一次atomへ結び直して本句の意味の枠にする。scene が `found=false` または構造契約不合格でも、一次 source atom が3要素以上あれば従来どおり本句は共通生成器へ進める。固定カタログ句へ切り替えるのは LLM 自体が利用できない場合だけとし、scene の弱さを理由に実測と無関係な固定句を生成句として発話しない。

### Structured JSON の境界

自動川柳の `haiku_draft` / `haiku_irony` / `haiku_scene` / `haiku_line_grounding` / `haiku_line_regeneration` は、2026-08-16時点の完成版と同じく各ドメインの検査器と最大6回の内容再生成で扱う。JSONの包み方だけを理由に共通clientで再生成・打切りしない。scene の一節objectと `clauses` 配列、行照合の一件objectと `assessments` 配列は、それぞれ同じ consumer が既知 atom ID・対象行・内容を検査する。

行照合の `assessments` が一部しか返らなければ、欠けた行だけを一行ずつ再照合する。候補外atom ID、意味不保持、不自然な日本語、音数違反等は行の不合格理由として扱い、合格行と使用済み材料を保持したまま不合格スロットを最大6回作り直す。初稿はこの6回とは別なので、同じ不合格スロットには初稿を含め最大7候補が生じうる。

workshopの共同編集step・採否・修正差分やassist等は、Rustの各ドメイン検証器を通す。外形と原文根拠の検査に通らなければ状態変更や操作を行わない。旧workshop分類器へのfallbackは終了し、初手・実検査後とも観測に対応するコード固定返答へ戻す。自動川柳の創作再試行と、状態変更を伴う限定抽出のfail-closedを同じ終了条件にしない。

現行契約にない旧 `haiku_workshop_material_pick` は削除済みである。句中の意味質問は、保存済み行別出典だけをコードで参照する。

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

### 実環境の発句間隔

2026-08-13 の比較実験では一時的に 3 分へ短縮したが、2026-08-16 に通常の **10 分**（`600000ms`）へ戻した。脅威・プレイヤー入力・他発話後の 30 秒静寂は別に維持する。

短期比較で再び間隔を縮める場合はローカル環境変数で明示的に上書きし、比較終了後は10分へ戻す。設定既定と `.env.example` は通常値の10分を正とする。

### カタログ原文と source atom

保存済みカタログ JSON は、通常会話と将来の **「ドギドのあんちょこ」** が読む知識の正本でもある。川柳の都合で `note: str` を配列へ変えたり、元 JSON へ atom を書き戻したりしない。

- `japanese` / `note` の原文は変更しない
- 発句時に観測された ID だけを `catalog_type:catalog_id` へ結び、原文 snapshot を作る
- `note` は実行時だけ `。！？!?` の直後で分ける。読点 `、` では分けず、終止記号も原文に残す
- 名前、各 note 文、mob の `poetic` 原文フィールド、実測した時刻・天気・場所・手元・周辺物・mob を、追跡可能な atom として扱う
- irony の内部要約をそのまま出典にはしない。scene はその詩的な芯を失わない自然な関西弁の1〜3節を structured で返す。各節は元になった一次 atom ID と `factual` / `interpretive` を持つ
- `factual` の主張範囲はコードが一次 atom から継承する。`identity_only` は名称、`source_meaning` は原文の意味、`observed_state` は現在の実測まで。`interpretive` は `poetic_interpretation`（印象・取り合わせ）だけで、新しい事実の断言には使えない
- scene の各節はコードで既知 atom ID・主張区分・文字数を検査し、通った節を `preface_clause` atom として保存する。その全体を `poetic_interpretation` atom にも束ね、どちらも派生元の `basis_atom_ids` を失わない
- カタログ名は全文の復唱を要求しない。意味保持の検証でその行の出典と確定した `catalog_label` に限り、ラベル中の4文字以上のかな語と一字だけ違う断片をコードで訂正する（例: `シラカバの階段` を根にした `しろかばの` → `しらかばの`）。全カタログへの曖昧検索、短語、複数候補、挿入・削除は自動訂正しない
- `preface_clause` は `preface:spoken` という別 provenance で保存する。句の行間重複を判定するときは派生 atom の ID ではなく `basis_atom_ids` を予約し、同じ一次材料を直接／個別の見どころ節経由で二重利用させない。`poetic_interpretation` は一句全体を覆う意味の枠なので、三行で共有できる
- 生成済みの各行には採用した atom ID と原文 snapshot を添え、`haiku_entries.jsonl` の `materials_snapshot` に保存する

scene の `summary`、旧 `preface_interpretation`、`claim_class` / `claim_scopes` / `basis_atom_ids` を欠く保存 atom は受け取らない。一方、一節分の `text / basis_atom_ids / claim_class` と、行 grounding の単体 object は情報が欠けていないため、配列で包み直して同じドメイン検査へ通す。過去の句本文・講評は残るが、出典契約を証明できない旧句から自動修正案は作らない。

たとえば古代の残骸の `note` は、原文を保持したまま実行時だけ次の三要素になる。

1. `ネザーに生成される珍しい鉱石。`
2. `茶色で、ひび割れている。`
3. `しかし、非常に高い爆発耐久値を持っており熱に強い。`

同じ一次要素を複数行の主な出典にはしない。先に合格した行が使った一次 atom は予約し、後続行の再生成候補から除外する。ただし、一次材料へ照合済みで実際に話した `poetic_interpretation` は、句全体の情景として三行で共有できる。カタログに `note` がない物について、名前から性質や音を事実として断言する atom は捏造しない。

意味の言い換え、検証済みの詩的解釈との整合、日本語の自然さは LLM が判定するが、候補外 ID の拒否、一次材料の重複排除、生成スロットの展開、最大試行回数、発話・保存の可否はコードが決める。再生成へ返す失敗理由は `meaning_not_retained`、`unnatural_japanese`、`source_reused`、音数／hard制約違反、候補重複などの閉じたコード値である。同一候補は Unicode 正規化・カナ統一・空白／句読点除去後のsignatureで即時棄却する。意味の近さを固定語リストで判定する旧 H6 は復活させない。

workshop は、この発話前ゲートを通った句に対する好み・表現・場面違和感を一緒に扱う場所である。壊れた句を先に出して workshop に修理を任せる品質ゲートではない。

### workshop の Locate → Edit → Test

三行だけの川柳では、コード用ASTや汎用サブエージェントを持ち込まず、**一行を構造ノード**として扱う。行の正本は `line_id / line_index / position / canonical_name / surface_text / reading_text / source_atom_ids / source_atoms / provenance` を束ねた一オブジェクトである。`surface_text` はモデルやプレイヤーが出した漢字・カタカナを含みうる表記、`reading_text` はTTS・音数・比較に使う確定ひらがなであり、両者は同一句の二表現である。役割は呼び出しとコード境界で分離する。

自然な相談の入口は、個別のintent分類器と返答表ではなく`haiku_workshop_agent_step`である。現在句、未採用案、この一句の直近4往復、保存済み出典、直近のaction結果をまとめて読み、`respond / explain / ask / inspect / propose_revision / compare / show_current / stage_player_edit / accept_pending / reject_pending / close_workshop / unrelated`から次の一手を一つ選ぶ。説明だけなら一手で終了し、必要なときだけコードの`reading / meter / source`検査または既存editorを実行する。各実結果を戻した後の再計画は一度だけ、全体は最大3 stepで、長い思考文、任意tool call、状態変更権限は持たない。会話理解には音声解釈を使えるが、局所編集／採否／終了は音声認識原文にも行為を示す根拠がなければ実行せず、疑問・否定・条件・引用・伝聞を拒否する。採否＋終了は各意思をそれぞれ検証する一つのtransaction、`unrelated`は同じ入力の通常雑談返答が成立したときだけ既存二回driftへ数える。

1. 共同編集stepが、会話の流れから修正を選んだ場合だけ、プレイヤー発話の対象行・一意な断片・問題種別を locate する
2. コードが対象行を確定し、固定行・使用可能atom・発句時hard制約を閉じる
3. haiku route の編集AIは全文ではなく、`line_index` / `expected_text` / `replacement_text` / `atom_ids` の差分だけを返す
4. コードが `expected_text` と現在の元行の完全一致、対象外行の不変、候補の実変更、一意な出典を確認する
5. 別structured評価と共通検査器が、意味保持・自然さ・音数・hard制約・出典重複を確認する
6. 不合格なら、コードで確定した行別失敗理由と不合格案を次の編集AIへ返す。前と実質同じ差分は別評価を呼ばずコードで棄却する
7. 合格案も未保存の `pending_revision` に置く。自然な採否は同じ共同編集step（利用不可時は旧pending専用schema）が閉じたactionと今回発話のevidenceへ抽出し、コードがそのevidenceを音声認識原文へ再照合したうえで、同じ差分を同じ元句へ適用できるか再確認してから保存する

これは行単位の compare-and-swap であり、対象が0件・複数解釈・元行不一致・一部だけ成功のときは元句を維持する。旧 `{line_index, text}` 応答は受け付けない。内部再試行は最大2回で、二回目は同じpromptの再送ではなく `meaning_not_retained`、`unnatural_japanese`、音数、出典、差分契約などの閉じた失敗理由を受けた再編集である。採用済み revision には `edit_contract` と検証済み `edits` も残し、後から局所修正の成功傾向を監査できるようにする。

修正案の句本文・採用語・保存可否はコードが固定する。共同編集stepは検証結果に沿った前置きだけを話せるが、句本文の復唱・別案への書き換え・保存済みという断言は禁止し、不成立時は実観測に対応する短い定型へ戻す。

プレイヤー自身が置換語を述べた場合、常駐会話モデルは句を生成せず、発話中の `replacement_text` / `evidence` と現在句中の `target_fragment` だけを抽出する。コードが発話根拠・対象行の一意性・ひらがな化・正確な5/7/5音・hard制約・CASを確認し、対象行オブジェクトの `surface_text` と `reading_text` を同じ操作で置換する。合格した三行だけを未保存案として提示し、採用時も両表現と出典を一緒に昇格する。

句中の意味を尋ねられた場合は、質問と一意に対応した行オブジェクトの保存済み `source_atoms` だけを説明根拠にする。行の出典が `poetic_interpretation` で、質問中にその派生元の一次材料名が明示されている場合だけ、保存済み `basis_atom_ids` を辿ってその材料名を答える。全材料候補からもっともらしい語をAIに選ばせない。行対応または出典が確定できなければ、別材料を捏造せず「オレにも分からん」と正直に返す。

### 発話の語順（[voice-delivery-plan.md](voice-delivery-plan.md)）

**実装済み（LLM preface 経路）:**

```text
Frame N:   irony → 見どころ一言（自分の世界モード開始・音声再生開始）
Frame N+1: scene根拠整理 + 構造化発句 + 行別検証 → 本句（モード解除・workshop 可）
```

- irony の `description` はそのまま先行発話できる自然な関西弁で生成し、確定した時点で音声キューへ送る。次フレームの scene は、その発話を一次atomへ結び直して本句生成へ渡す
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

- バイオーム・分類・地形メタデータ（空が見える場合、または洞窟固有バイオームの場合だけ句へ投影）
- 時間・天気（空が見える場合だけ句へ投影）
- 現在 Y、バイオーム基準気温、降雪開始高度からコードで確定した現在地の降水なし／雨／雪・雷・降雪環境（数値は LLM へ渡さない）
- 周辺の `snow` / `snow_block` / `powder_snow` 実測（地表の積雪根拠）
- 周辺ブロック上位
- 手持ち
- ホットバーに限らない全インベントリから選んだ近縁2件と異質な1件
- 平和 mob
- mob の `poetic` tag
- ローカルで組み立てた `candidate_tensions`

LLM には「選ぶ」「まとめる」「詠む」だけをさせる。

候補と source atom は、周辺ブロック・手持ち・全インベントリから選んだ物を先に、バイオーム・時刻・天候などの背景を後に並べる。ゲーム状態としてバイオームや天候を観測していても、空が見えない場所では地表の天候・時刻を川柳材料へ出さず、洞窟固有でない地表バイオームも出さない。この投影条件は発句時の materials に保存し、workshop で背景情報を復活させない。

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

- 川柳・雑談の**保存はコード**（`.dogido_memory/` の JSONL）が正本。共同編集agentは保存候補を選べても直接書き込めない
- 通常workshopだけ、閉じたactionと実検査を持つ最大三stepの共同編集agentを使う。句正本・CAS・音数・hard制約・採否・保存・戦闘中断はコード所有
- 汎用エージェント基盤（例: Hermes）は**使わない**。任意tool実行・長い自律loop・長期思考履歴は機能過剰
- irony 抽出 → 一句生成 → fallback のような LLM 経路も、既存 route と閉じた型・コード検証で小さく構成し、特定の汎用ワークフロー基盤を前提にしない
- panic / cue / 状態遷移は引き続きRustのコード側（状態機械 + 優先規則）が担当し、LLM ワークフローへ移さない
