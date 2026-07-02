# コードレビュー: プレイヤー入力優先強化・状況理解・ハルシネーション対策など

**日付**: 2026-07-02  
**レビュワー**: Grok Build
**対象プロジェクト**: DokiDoki-Dogido (ドギド)  
**レビュー範囲**: プレイヤー入力の扱い、プロンプト構造、state machine 決定フロー、entity 扱い、kill ロジック、重複など。  
**方針**: コードは一切変更せず、分析と修正計画（diff 形式で具体案提示）に集中。後日確認用に本ファイル保存。

## 全体サマリー

現在の最大問題「プレイヤーの入力への反応が弱い」

### 主な原因

1. **プレイヤー入力の優先度が「抑制」止まりで「理解の優先」になっていない**
   - `player_input_priority_cooldown_ms` (120s) は自発的な環境反応（バイオーム・構造物・haiku・暗闇アドバイスなど）を止めるだけ。
   - プレイヤー発言自体の**文脈理解**が極めて薄い（biome/time/structure だけ渡している）。
2. **プレイヤー発言は ambient flow の一部**として扱われ、環境トリガーと同等か後回しになりやすい。
3. **状況スナップショットがプレイヤー質問向けに整備されていない**。
4. **エンティティ知識が LLM 丸投げ** → 捏造（ハルシネーション）しやすい。
5. **感情・ノリ反応がカタログ/特定イベント中心**で、戦闘中の「オラオラ」ノリや雑魚撃破の喜びが弱い。
6. **レア度システムが不在**。 ->どういうふうにレア度つける？何段階が適切？
7. **倒した判定ロジックが複数箇所に散在**（特に boss 以外も含めると将来的に痛い）。

**良い点**:
- `player_input/` によるルーティング/ガードレールは既に存在し、一定のキーワード質問（敵の数、ドラゴン方角）はちゃんと特別扱い。
- `_player_input_priority_active` + `breaks_silence` の仕組みは方向性として正しい。
- visual_threats / combat state の構造化データは豊富（距離・方向・entity_id・on_fire など）。
- py_trees ポリシーへの移行が進んでいて、将来的な明確な優先順位ツリーが作りやすい。

以下、優先度順に詳細 + 修正計画（diff）。

---

## 1. プレイヤー入力優先ロジックの根本強化（最優先）

### 現状

**主要フロー** (`dogido_server/state_machine/machine.py:55`):
```python
self.player_input = route_player_input(event.meta.user_text)
...
actions = self._build_actions(...)
```

**ambient 決定の核心** (`environmental_reactions.py:365` 付近):
```python
if self.player_input.asks_hostile_count:
    ...
if self.player_input.asks_dragon_direction:
    ...
...
if self._has_pending_player_chat(event):
    return self._speech_actions(self._render_player_chat_reply(event))

if self._player_input_priority_active(now):
    return []   # ここで以降の自発発話を殺す
```

`_player_input_priority_active` (`common.py:443`):
- `breaks_silence` または `last_player_input_at` からの cooldown (120s) で True。
- `should_block_ambient` は単に `bool(normalized_text)`。

**プレイヤーチャット用コンテキスト** (`narration.py:389`):
```python
details = {
    "user_text": ...,
    "biome": ...,
    "structure_label": ...,
    "time_phase": ...,
    "mode": ...,
}
# → _build_player_chat_messages (prompts.py:490)
```

**問題**:
- プレイヤー発言が来ても「今周りに何がいるか」「どの方向を見ているか」「最近何を倒したか」などがほぼ渡っていない。
- バイオーム/構造物入場コメント、haiku、特殊バイオームなどは `pending_*` 状態で保持され、プレイヤー入力と同じイベントで競合しやすい。
- cooldown は「話しかけられた後しばらく黙る」だけで、**プレイヤーの発言内容を最優先で理解する**仕組みではない。
- `/` コマンドは優先ミュートを発動させない（意図的）。

### 提案

**A. 優先度階層の明確化（最上位に Player Query Layer を置く）**

1. イベント到着時 → まず PlayerInputIntent を分類（rule-based + 将来的 LLM intent）。
2. Intent が "situational_query" / "combat_reaction" / "chat" なら、**即座に専用パスで処理**し、他の pending (biome/structure) をこのターン抑制。
3. 通常の環境 pending は player input イベントでは後回し or キャンセル。

**B. プレイヤー会話用に大幅にリッチなコンテキストを渡す**

**C. cooldown の用途を「自発抑制」専用にし、プレイヤー返事自体は常に高優先で通す（現在も概ねそうだが、競合を根絶）。**

### 推奨変更 diff 例

```diff
diff --git a/dogido_server/player_input/types.py b/dogido_server/player_input/types.py
index ...
--- a/dogido_server/player_input/types.py
+++ b/dogido_server/player_input/types.py
@@
 @dataclass(slots=True)
 class PlayerInputContext:
     ...
     asks_save_last_haiku: bool = False
     player_haiku_text: str | None = None
+    # NEW: より強い優先度フラグ
+    is_direct_address: bool = False          # 名前呼びや「ドギド」など
+    is_situational_query: bool = False       # まだいる？ これ何？ 走ってるの？
+    is_fight_excitement: bool = False        # オラオラ、うおお、がんばれ系
```

```diff
diff --git a/dogido_server/state_machine/mixins/environmental_reactions.py b/dogido_server/state_machine/mixins/environmental_reactions.py
index ...
--- a/dogido_server/state_machine/mixins/environmental_reactions.py
+++ b/dogido_server/state_machine/mixins/environmental_reactions.py
@@
     def _ambient_environmental_actions(...):
+        # === 最優先: プレイヤー入力 ===
+        if self.player_input.breaks_silence:
+            # キーワード系は既存のまま
+            if self.player_input.asks_hostile_count:
+                ...
+            if self.player_input.asks_dragon_direction:
+                ...
+
+            if self._has_pending_player_chat(event):
+                # プレイヤー会話は環境 pending をこのターン無視して即答
+                self._clear_environmental_pending_for_player_priority()
+                return self._speech_actions(self._render_player_chat_reply(event))
+
+        if self._player_input_priority_active(now):
+            return []
+
         # 以降は今まで通り（biome/structure/haiku など）
```

```diff
diff --git a/dogido_server/state_machine/mixins/narration.py b/dogido_server/state_machine/mixins/narration.py
index ...
--- a/dogido_server/state_machine/mixins/narration.py
+++ b/dogido_server/state_machine/mixins/narration.py
@@
     def _render_player_chat_reply(self, event: GameEvent) -> str:
         ...
         details={
             "player_name": ...,
             "user_text": ...,
-            "biome": ...,
-            "structure_label": ...,
-            "time_phase": ...,
-            "mode": ...,
+            "biome": ...,
+            "structure_label": ...,
+            "time_phase": ...,
+            "mode": ...,
+            # === 追加（強く推奨） ===
+            "nearby_hostiles": self._summarize_visual_threats(event.visual_threats),
+            "nearby_passive": [m.type for m in event.passive_mobs[:5]],
+            "combat_state": {
+                "hostiles_within_10": event.combat.hostiles_within_10,
+                "recent_damage": bool(event.combat.recent_damage_ms),
+            },
+            "player_health": event.player.health,
+            "looking_direction": self._current_looking_summary(event),
         }
```

**prompts.py 側の player_chat プロンプトも大幅強化**（後述の状況理解と連動）。

---

## 2. 状況理解の改善（「まだいる？」「あそこで走ってるのは何？」「これ何？」）

### 現状

- `asks_hostile_count` / `asks_dragon_direction` は guardrails で検知 → 専用レンダラ（rule-based、30マス以内カウントなど）で対応。良い。
- それ以外は全部 `_render_player_chat_reply` → 薄いコンテキストで LLM に投げる。
- 「走ってる」→ 現在 `approaching` フラグはあるが、velocity や「直前に動いた」情報は薄い。
- 「これ何？」→ 静的なもの（ブロック・アイテム・レアな passive/structure 内のもの）に対する専用パスはほぼなし。LLM が適当に答える。
- **Vision**: 構造化された visual_threats はあるが、**実際のスクリーンショットを LLM vision に渡す仕組みは存在しない**（grep で vision/screenshot 関連の active コードはほぼ無し）。

### 問題

- LLM に「周囲の mob リスト」と「ユーザーの曖昧参照」を同時に与えていない。
- レア度の高いエンティティを「これ」と言われたときに優先的に答える仕組みがない。

### 提案

**短期**:
- guardrails を拡張（「走ってる」「動いてる」「あそこ」「これ」「何」「どれ」などを situational query としてマーク）。
- `_render_player_chat_reply` を状況別ルートに分割（または intent で分岐）:
  - Hostile presence query → 既存強化版
  - Movement query → 視線方向 + 近い moving threat を優先
  - "これ何？" / pointing → 最も近い or レア度の高い visible entity を選んで答えるロジックを追加

**中期**:
- visual_threats / passive_mobs に `is_moving` や最近の動き情報を adapter 側から追加可能なら入れる。
- プレイヤー視線方向（yaw/pitch）で「その方向のもの」をフィルタするユーティリティ追加。

**Vision 評価**:
- Minecraft mob 視覚認識: 現在の adapter が正しく `type` を送ってくれている前提なら、**構造化データで十分**なケースが多い。
- ただし「これ何？（プレイヤーが指差した静的なものや遠くのもの）」「走ってるのは変な mob？」のようなケースでは screenshot + vision model が強力。
- **推奨**: 即時導入は不要（latency・コスト）。まずは構造化データ + whitelist で 80% 狙う。必要になったら `meta.screenshot_b64` や専用 vision route を追加する形が良い。

### 具体案 diff 例 (guardrails 強化)

```diff
diff --git a/dogido_server/player_input/guardrails.py b/dogido_server/player_input/guardrails.py
index ...
--- a/dogido_server/player_input/guardrails.py
+++ b/dogido_server/player_input/guardrails.py
@@
 HOSTILE_COUNT_QUERY_KEYWORDS = ...
 DIRECTION_QUERY_KEYWORDS = ...
+MOVEMENT_QUERY_KEYWORDS = ("走っ", "動い", "走って", "飛んで", "泳いで")
+THIS_WHAT_KEYWORDS = ("これ何", "これなに", "これは何", "なんやこれ", "どれ", "あれ何")
+...
 
 def asks_hostile_count(...): ...
 
+def is_situational_query(normalized_text: str) -> bool:
+    ...
+    if any(k in normalized_text for k in MOVEMENT_QUERY_KEYWORDS + THIS_WHAT_KEYWORDS):
+        return True
+    return False
```

---

## 3. ハルシネーション対策（特に mob 名の捏造）

### 現状

- `sanitize.py`: 日本語チェック、禁止語句、繰り返し除去などはしっかりしている。
- **しかし「知らない mob 名を出さない」仕組みは存在しない**。
- `minecraft_ids.py` は単なる normalize。
- mob_list.md や entry_catalog は人間向け / 部分的にしか使われていない。
- LLM プロンプトに「以下の mob 以外は絶対に名前を出さない」と明記していない（player_chat 含む）。

### 提案

1. **中央ホワイトリスト** の作成（`minecraft_ids.py` や新 `entity_whitelist.py` に全既知 mob を列挙）。
2. LLM プロンプトに必ず「使用可能エンティティリスト」を渡す（または "only refer to entities from this list"）。
3. 出力後 **post-filter**:
   - 未知の固有名詞（mob 名っぽいもの）が出たら fallback か再生成 or 一般名に置換。
4. レア度システムと連動して「このリストの中で最もレアなもの」だけを強調して答える。

```diff
diff --git a/dogido_server/minecraft_ids.py b/dogido_server/minecraft_ids.py
index ...
--- a/dogido_server/minecraft_ids.py
+++ b/dogido_server/minecraft_ids.py
@@
+KNOWN_HOSTILES = {
+    "creeper", "zombie", "skeleton", "spider", "enderman", "warden", ...
+}
+KNOWN_PASSIVE = { ... }
+ALL_KNOWN_ENTITIES = KNOWN_HOSTILES | KNOWN_PASSIVE | ...
+
+def is_known_entity(name: str | None) -> bool:
+    n = normalize_minecraft_id(name)
+    return n in ALL_KNOWN_ENTITIES if n else False
+
+def filter_known_entities(candidates: list[str]) -> list[str]:
+    return [c for c in candidates if is_known_entity(c)]
```

プロンプト強化例（prompts.py `_build_player_chat_messages`）:

```diff
 user_prompt = (
     ...
     f"使用してよいエンティティ名だけ: {', '.join(known_list)}"
     "知らないモブの名前は絶対に出さない。わからない場合は「なんか変なの」くらいでごまかす。"
 )
```

---

## 4. 感情・ノリ・リアクションの強化

### 現状

- 戦闘中は主に `ushiro_call`, `hostile_massive`, `persistent_*` などの固定/ catalog 反応。
- after math は LLM（`aftermath` route）で hostiles リストを渡しているが、主にボス寄り or 一般でも控えめ。
- 「オラオラしてる時にノる」「ザコを倒した時の喜び3パターン」「[名前]！おつかれー！」などはほぼ未整備。
- 敵がまだいるのに「怖い」発言が出るケースは、priority や `last_confirmed_hostiles` の管理で一部抑制されているが不完全。

### 提案

- `combat.json` に **fight_excitement** / **kill_celebration_generic** カテゴリを追加（3〜5 パターンずつ）。
- LLM aftermath を「ボス用」と「一般戦闘後用」に分けるか、details に `is_boss` + `was_ordinary_mob_fight` を渡す。
- player_input で fight_excitement 検知したら、**LLM ではなく軽い catalog または専用 prompt で即ノリ反応**。
- 敵残存チェックを強化した上で「まだおるで！」を出す。

例 catalog 追加イメージ:

```json
"kill_celebration_generic": [
  "{player_name}！よっしゃ！{label}倒した！まだおるで！",
  "おお、{label}やったな！ええ感じや！",
  "よし、1体片付けた！"
]
```

---

## 5. レア度システムの導入検討

### 現状

- なし。すべての mob が平等に扱われる（cooldown 別、ambient では種ごと cooldown のみ）。

### 提案設計

**データ側**:
- `data/catalogs/mobs/rarity.json` または各 mob entry に `"rarity": 1-5` (5 = Warden / Dragon / 極レア) を追加。

**ロジック側**:
- ambient_mob 選択時、レア mob を優先（スコア = 1 / cooldown_factor + rarity_bonus）。
- プレイヤー会話コンテキストに `"rare_entities_nearby": [...]` を明示的に入れる。
- 「これ何？」で複数いたらレア度の高い方を答える。
- 話題優先度にも寄与（レア mob が見えたら biome コメントより優先）。

**簡単な rarity 統合例** (ambient 選択):

```python
def _score_mob(self, mob, now):
    base = 1.0
    if self._rarity(mob.type) >= 4: base += 2.0
    ...
```

---

## 6. ロジック二重化・重複コードの排除

### 現状の主な重複

- **倒した判定** (`last_confirmed_hostiles` + `COMBAT_ENDED` + `warden_defeat_confirmed` / `dragon_defeat_confirmed`)
  - `common.py`: `_boss_defeat_confirmed`
  - `state_updates.py`: 複数箇所で更新・クリア・pending 設定
  - `action_builder.py`, `py_tree_policy.py`, `narration.py` でそれぞれ似たチェック
- after math 発行条件が legacy と py_tree でほぼ同じロジックが二重実装。
- 各種 cooldown チェックが散在。

### 提案

1. **単一ソース化**:
   ```python
   def get_confirmed_defeated_hostiles(self, event: GameEvent) -> list[str]:
       ...
   ```
2. `NarrationMixin` や `CommonMixin` に `emit_aftermath_if_needed` を1箇所に。
3. 将来「メッセージ生成を LLM に完全に任せる」場合の設計:
   - state machine は **事実の事実のみ** を出力（"defeated": ["creeper"], "current_threats": [...], "player_input": "..."）。
   - LLM は "generate_reaction( facts )" だけを呼ぶ形に。
   - 現在のような "kind" ごとの細かい prompt builder を減らし、context builder + single "narrate" route へ移行。

---

## その他の観察・推奨

- `py_tree_policy.py` の NormalEnvironmentEvent が肥大化している。プレイヤー優先を最初に判定する Condition を明確にトップに置くべき。
- プレイヤー入力は `event.meta.user_text` に相乗りする設計は悪くないが、専用 `PlayerChatEvent` を分離した方が将来的にクリーン。
- テスト（test_player_chat.py など）で「プレイヤーが『まだいる？』と言ったときに正しい数を答える」ケースを増やす。
- ドキュメント（current-spec.md, behavior-spec.md）に「プレイヤー発言の優先順位は環境イベントより常に高い」と明記する。

---

## 修正実装推奨順序（PR 計画風）

1. **P0**: player_input context 強化 + `_has_pending_player_chat` / ambient での即時優先処理 + 入力時 pending クリア（#1 の基盤）。
2. **P0-P1**: player_chat 詳細コンテキスト注入（nearby_hostiles, combat_state など）。
3. **P1**: guardrails に situational query 拡張 + 専用回答ルート。
4. **P1**: 既知エンティティホワイトリスト + sanitize / prompt での使用強制。
5. **P2**: combat.json に kill_celebration_generic / fight_reaction 追加 + ノリ検知。
6. **P2**: レア度メタデータ定義 + 選択ロジックへの反映。
7. **P3**: 倒した判定の単一関数化 + コメント整理。
8. **P3以降**: py_tree ツリーの優先順位 Condition 明確化。Vision 検討（必要なら）。

---

## 結論

プレイヤー入力を「ただの割り込み」から「最優先の対話文脈」へ昇格させるのが全改善の核心です。

現在のアーキテクチャ（state machine + 構造化イベント + LLM leaf）は優秀なので、**コンテキストの豊かさ**と**決定順序の厳密さ**を足すだけで大幅に改善するはずです。

本レビューは `/docs/research/code-review-player-reactivity-2026-07-02.md` に保存済み。必要に応じてこのファイルを基に実装計画を立ててください。

---

*レビュー担当: Grok (コード改変なし、分析 + 計画のみ)*