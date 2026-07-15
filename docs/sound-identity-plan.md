# 音の正体・モブ名の扱い（計画ドラフト）

**日付:** 2026-07-15  
**状態:** 実験コードは撤回済み。**実装前の設計メモ**。  
**きっかけ:**  
1. LLM が「野犬」など Minecraft にいない生物名を言う  
2. 「敵意っぽい気配／敵意っぽい音」というサーバ側の曖昧ラベルが不自然  
3. 禁止リスト（レキシコン）や allowlist の継ぎ足しは設計としておかしい、という指摘

関連: [event-schema.md](event-schema.md) §12、[dialogue-design.md](dialogue-design.md) 音・気配、adapter `DogidoClientAdapter` の sound 経路。

---

## 1. やったこと・戻したこと

| 試み | 判定 |
|---|---|
| 「野犬」など個別禁止 | ❌ 対症療法。次の誤名でまた増える |
| リアル動物レキシコン × カタログ差分で弾く | ❌ 弾く側の辞書が本丸になってしまう |
| 音から allowlist を組み「敵意っぽい気配」に寄せる | ❌ 方針は近いが、曖昧ラベルが先に立ってしまった |

**方針転換:** 出力を「弾く」のではなく、**adapter が既に知っている sound → mob 対応をそのまま使う**設計を、事実確認してから書く。  
実験的な player_chat 音まわりの改修は **一旦 HEAD 相当に戻した**（ベッド付き緊急シェルター修正は別件として残置）。

---

## 2. 事実関係（コード上わかっていること）

### 2.1 Adapter は「ターゲティング」も「サウンド」も持っている

Fabric client adapter（`DogidoClientAdapter`）:

- **視認脅威** `visual_threats` … type / 距離 / 方向
- **敵対音** `auditory_threats` … sound packet 由来 + ヒューリスティック  
  - `label`, `sound_event`, `spoken_name_allowed`, 方向, distance_band
- **非敵対周囲音** `ambient_sounds` … 村人・家畜など（戦闘には使わない）
- モブの **target がプレイヤーか** も client 側で見ている（例: `mob.getTarget()`）

つまり「正体が全く分からない」状態を、サーバが勝手に「敵意っぽい気配」とラベル付けして会話の主語にするのは、**データの貧弱さというより設計の逃げ**になりやすい。

### 2.2 `spoken_name_allowed` と曖昧ラベル

スキーマ上:

- `spoken_name_allowed=false` の間は「発話で具体名を出さない」（未視認の断定を避ける）
- `label` の推奨値は `hostile_presence` / `hostile_voice_like` など**抽象ラベル**もあり得る

サーバの `player_chat` 要約（撤回前・現行 HEAD）は概ね:

```text
spoken_name_allowed かつ label あり → モブ名っぽく言う
それ以外 → 「敵意っぽい気配」
ambient type → MOB_LABELS / フォールバック
```

ここがユーザー体感の **「敵意っぽい気配って何だい？」** の出所。  
「ターゲットされたら MC から情報が来る」という運用感覚と、**未視認音は名を伏せる**というスキーマ思想が衝突している。

### 2.3 野犬問題の本当の経路（仮説）

「タイガの朝やから、野犬が遠くで鳴いてるだけちゃうかな」は典型的に **player_chat + LLM**:

1. 音メモが空／曖昧／または type が弱く伝わっている  
2. バイオーム（タイガ）から LLM が「野犬」連想で補完  
3. サーバはモブ json と突き合わせずそのまま発話  

禁止リストで「野犬」だけ潰しても、次は別のリアル動物名になる。

---

## 3. 設計の軸（合意したいこと）

### 原則 A — 種名のソース・オブ・トゥルース

| 優先 | ソース | 種名の扱い |
|---|---|---|
| 1 | `visual_threats[].type` | カタログ label で言ってよい |
| 2 | `auditory_threats` の **解決済み mob type**（sound_event または label がモブ id のとき） | ポリシー次第で名前 or ぼかし |
| 3 | `ambient_sounds[].type` がモブ id | カタログ label |
| 4 | 解決できない | **種名を出さない**（「なんか声」程度）。「敵意っぽい気配」固定句に依存しない |

**種名を LLM に発明させない。**  
「カタログから妥当なものを選ぶ」のは、**音が既に type を持っているとき**のマッピングであって、タイプ不明時にカタログをランダム抽選することではない。

### 原則 B — 「弾く」より「渡さない」

- プロンプトに無い種名をモデルが言いがちなのは、**曖昧メモ + バイオーム連想**が原因  
- 対策の第一は **hearing に載せる文字列を、adapter の type から機械的に決める**こと  
- 後段 sanitize の巨大禁止リストは採用しない

### 原則 C — ターゲット情報を捨てない

計画フェーズで adapter を再確認し、次を切り分ける:

1. **プレイヤーを target にしている hostile** がクライアントで分かるなら、音と独立に `visual` または専用フィールドで送れているか  
2. 音だけ・遮蔽だけ・ターゲットあり、の優先順位  
3. `spoken_name_allowed=false` を「常に名を伏せる」ではなく、  
   - ターゲット中 / 最近視認 / sound_event がモブ確定  
   のときは **true にして type を載せる** 方が自然ではないか  

「敵意っぽい気配」を会話に出すのは、上記のどれにも当てはまらないときだけ、という整理を目指す。

---

## 4. 調査タスク（実装前）

### S1. 実プレイ 1 ケースの生 JSON

野犬発言が起きた前後の `status_snapshot` を 1 件保存:

- `auditory_threats` の label / sound_event / spoken_name_allowed  
- `ambient_sounds` の type / sound_event  
- `visual_threats`  
- 可能なら combat / target 関連  

**目的:** LLM の妄想か、adapter が wolf と誤ラベルしているか、メモ空かを切り分ける。

### S2. Adapter の sound_event → type 対応表

`DogidoClientAdapter` 内のマッピングを洗い出し:

- `entity.zombie.*` → zombie になっているか  
- wolf との取り違えが無いか  
- `spoken_name_allowed` がいつ true になるか  

### S3. ターゲット情報の送信有無

`getTarget() == player` の結果が、イベントのどのフィールドに載るか（または載っていないか）。  
載っていなければ **スキーマ拡張案** を別途書く（実装は合意後）。

### S4. player_chat に渡している文字列の監査

現行 `_player_chat_hearing_summary` / threat_summary が、  
曖昧ラベルをどの頻度で生成しているかをログまたはテストで列挙。

---

## 5. 実装方針（調査後に確定する案）

### フェーズ 0 — 現状維持（いま）

- 実験的な禁止リスト・allowlist 継ぎ足しはしない  
- ベッド持ちの緊急シェルター分岐など、無関係な修正は残してよい  

### フェーズ 1 — 観測の正しさ（adapter）

- sound_event → モブ id の対応を正し、ゾンビを狼扱いしない  
- ターゲット中は type を隠さない（`spoken_name_allowed` ポリシー見直し）  
- 必要なら `targeting_player: true` や `resolved_mob_type` を明示フィールド化  

### フェーズ 2 — サーバはマッパーだけ

```text
sound / threat type id
  → entry_catalog.mob_entry / label
  → hearing 行に「ゾンビの音（奥）」など
  → 解決不能なら「奥の方で音（種別不明）」※固定の「敵意っぽい気配」は使わない or 稀に
```

LLM への指示は短く:

- 音の話はメモの種名だけ  
- メモに種名が無いとき種名を当てない  

### フェーズ 3 — 必要なら最小ガード

- **禁止レキシコンは作らない**  
- 任意: 出力に「音メモに無いカタログ種名」が出たら fallback（whitelist は **そのターンの hearing/visual 由来**だけ）  
- 一般知識の「ゾンビって燃える？」は visual/hearing が空でもカタログ名を許可する必要があるので、ガードは「音の断定」に限定する  

---

## 6. やらないこと

- リアル動物の長いブラックリスト運用  
- 不明音に対してカタログから「一番それっぽいモブ」を推測抽選  
- チート級の壁裏座標断定  
- 「敵意っぽい気配」をブランドフレーズとして量産  

---

## 7. 成功条件

1. ゾンビ音のとき、ドギドが「野犬」と言わない（種名はゾンビ or 種別不明）  
2. プレイヤーがターゲットされている／視認できるときは、曖昧ラベルに逃げない  
3. モブ json の label 追加だけで新しい正規名が会話に出る（禁止リスト更新不要）  
4. player_chat の音まわり変更が、小さなマッパー + 短いプロンプト規則に収まる  

---

## 8. 実装メモ（2026-07-15）

実ログより、問題は「player_chat が音を無視する」ではなく:

- hearing は **今フレームの** `auditory_threats` / `ambient_sounds` だけ見ていた  
- `recent_audio_ms≈7.6s` のように **ついさっきの音** は combat 側にだけ残り、会話に載らなかった  
- 空メモ + バイオーム連想で LLM が種名を捏造  

### 入れた対策（サーバ）

1. **`recent_hearing_memos` バッファ**（既定 12 秒、`player_chat_hearing_retention_ms`）  
2. 種名は **label / sound_event → mob カタログ解決** できたときだけ（例: `entity.zombie.ambient` → ゾンビ）  
3. 解決できない音は「種別未確定」で、リスト外の種名を当てないようプロンプト明示  
4. デバッグログ: `player_chat_hearing empty=… named=… auditory=… buffer=…`  

禁止レキシコンは採用しない。  

### まだ将来

- adapter のターゲット情報と `spoken_name_allowed` ポリシー見直し  
- 実サウンド対応表の監査（S2）
