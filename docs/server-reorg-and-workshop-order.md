# パッケージ整理 ＋ 川柳ワークショップ — 編集順

**日付:** 2026-07-16  
**状態:** Phase A〜D（H1〜H5.1）実装済み。H6 固定語 materials 突合は**撤回**。H7 / catalog 分割（Phase E）は未  
**方針:** [server-package-layout-proposal.md](server-package-layout-proposal.md)  
**workshop:** [haiku-player-improvement-plan.md](haiku-player-improvement-plan.md)  
**pin の閉じ方:** 同計画 §1b  

---

## 大原則（動かすとき）

1. **先に「新場所に中身を置く」→ 旧場所は re-export → import 置換 → 旧を薄く削除**  
2. **1 ステップごとに `pytest` が通る単位**で止める  
3. **mixin 巨大ファイルへの機能追加はしない**（フック 1 行＋新モジュール）  
4. 移動時は必ず:
   - 新ファイル作成  
   - 旧ファイルを `from new import *` または明示 re-export  
   - `rg "old.path"` で参照を洗い、必要なら新 path に更新  
   - テスト実行  

```bash
# 移動後の定番
rg "player_chat_policy|dialogue_context|catalog_readings|from dogido_server.entry_catalog" dogido_server tests -g'*.py'
pytest tests/test_player_chat*.py tests/test_haiku*.py tests/test_catalog*.py -q
```

---

## 全体の順番（俯瞰）

```text
【Phase A】箱だけ作る（壊さない）
    ↓
【Phase B】迷子の既存を dialogue/ へ（re-export）
    ↓
【Phase C】haiku/ 箱 + workshop の核（H1〜H3）
    ↓
【Phase D】workshop 完成寄り（H4〜H5）
    ↓
【Phase E】catalog 分割（任意・余裕があれば）
    ↓
【Phase F】mixin ダイエット（後回し）
```

**workshop 本体は Phase C から。**  
先に B を少しやって「dialogue の置き場」を固定すると後が楽。

---

## Phase A — 空パッケージ（編集順）

| 順 | 作業 | 触るファイル |
|---|---|---|
| A1 | ディレクトリ作成 | `dogido_server/dialogue/` `dogido_server/haiku/` （必要なら `memory/` は後） |
| A2 | 空の `__init__.py` | 上記 + 公開予定シンボルはまだ空でよい |
| A3 | テスト1本 or import 煙 | `python -c "import dogido_server.dialogue; import dogido_server.haiku"` |

**やらない:** 既存ファイルの移動（A ではまだ）。

---

## Phase B — 既存ポリシーを dialogue へ（編集順）

目的: ルート直下の `player_chat_policy` を正しい家に移す。

| 順 | 作業 | 詳細 |
|---|---|---|
| B1 | **新** `dialogue/chat_policy.py` | `player_chat_policy.py` の中身を **コピー**（まだ削除しない） |
| B2 | **旧** `player_chat_policy.py` を re-export のみに | `from dogido_server.dialogue.chat_policy import *` と `__all__` |
| B3 | 参照を新 path に置換（任意だが推奨） | `rg player_chat_policy` → narration, tests 等を `dialogue.chat_policy` に |
| B4 | pytest | chat / casual / policy 系 |
| B5 | （任意）`dialogue_context.py` → `dialogue/context.py` 同じ手順 | 旧 re-export |
| B6 | （任意）`player_input/` を `dialogue/player_input/` へ | **依存が多いので B の最後か別 PR。急がない** |

**失敗しやすい点:**  
循環 import。`chat_policy` は `entry_catalog` だけに依存する現状を維持。service を policy から import しない。

---

## Phase C — haiku 箱 ＋ workshop 核（H1〜H3）★本体

### C0. 箱の中身（ファイル配置）

```text
dogido_server/haiku/
  __init__.py          # open_from_emission, maybe_close, workshop_details を re-export
  workshop.py          # RecentHaikuWorkshop + lifecycle + 意図のたたき台
  # 後で:
  # context.py  ← haiku_context 移動は Phase F 寄り
```

### C1. H1 — pin オブジェクト（編集順）

| 順 | 作業 | ファイル |
|---|---|---|
| C1.1 | `RecentHaikuWorkshop` dataclass | **新** `haiku/workshop.py` |
| C1.2 | `open_from_emission(emission, materials, now)` | 同上 |
| C1.3 | Session にフィールド追加 | `service.py` の `SessionInfo` → `haiku_workshop: RecentHaikuWorkshop \| None = None` |
| C1.4 | 発句成功後に open | **`service.py`**（emission を session に渡す既存経路）または haiku mixin から service に載せないなら **emission 返却後の service 側 1 か所** |
| C1.5 | materials スナップショット | irony/scene 要約を dict で渡す。**haiku.py から材料 dict を組み立てて open に渡す薄い関数**を `workshop.py` 側に置く |
| C1.6 | テスト | `tests/test_haiku_workshop.py` … open で text/materials が入る |

**haiku.py の編集:**  
発句完了の直後に「materials を集めて返す／コールバック」程度。**lifecycle ロジックは書かない。**

### C2. H1b — close 条件（編集順）

| 順 | 作業 | ファイル |
|---|---|---|
| C2.1 | `maybe_close(ws, now=..., reason=...)` | `haiku/workshop.py` |
| C2.2 | 定数 | `T_open`, `T_idle`, `N_drift`（config でも workshop 内でも可。まずは workshop 定数） |
| C2.3 | close 理由 enum 文字列 | `next_haiku|explicit|praise|revise|drift|timeout|panic|session` |
| C2.4 | 発話処理の入口で timeout/drift を呼ぶ | **`service.py`**（player-input / process の共通点） |
| C2.5 | 次発句で旧 ws を close してから open | C1.4 と同じ箇所 |
| C2.6 | テスト | 時間・drift 連続・次発句 |

### C3. H2 — 入力ルーティング（編集順）

| 順 | 作業 | ファイル |
|---|---|---|
| C3.1 | `classify_workshop_intent(text) -> kind \| None` | `haiku/workshop.py`（後で `intents.py` 分割可） |
| C3.2 | 既存 formal は維持 | `player_input` の `直し:` / 読み / 保存はそのまま **先に**判定してよい |
| C3.3 | service の player 応答分岐 | `if workshop.open and (formal or classify): workshop path else chat` |
| C3.4 | **open 中でも** 句無関係は通常 chat + drift++ | close は C2 |
| C3.5 | テスト | open 中「〜って何」→ workshop、「インベントリ」→ chat + drift |

### C4. H3 — 返事と critique 保存（編集順）

| 順 | 作業 | ファイル |
|---|---|---|
| C4.1 | `memory.save_haiku_critique(...)` | `memory.py`（置き場は memory のまま。後で `memory/` パッケージ化は Phase E） |
| C4.2 | critique JSONL パス | `.dogido_memory/long_term/haiku_critiques.jsonl` |
| C4.3 | workshop 返事テンプレ | `haiku/workshop.py` の `render_workshop_reply(kind, workshop, player_text)` … 最初はルール文 |
| C4.4 | **materials 開示**を返事に含める | ask_meaning / critique 時 |
| C4.5 | player_chat に載せる details | `workshop_prompt_details(ws)` → 句全文＋材料。**open 中の通常 chat にも載せるか**は方針: **workshop 応答時は必須、通常 chat 時は載せてよいが短く** |
| C4.6 | テスト | critique が1行増える、返事に材料が出る |

### C 完了条件

- [ ] 発句後 session に pin がある  
- [ ] 履歴が押し出しても details で句が分かる  
- [ ] close 条件で pin が外れる  
- [ ] 「グーの木の水って何?」相当で materials 開示  
- [ ] `haiku.py` に workshop ロジックが増えていない  

---

## Phase D — workshop 仕上げ（H4〜H5）✅

| 順 | 作業 | 状態 |
|---|---|---|
| D1 | 自然文直し → revision（`extract_conversational_revise`） | **済** `haiku/workshop.py` + service |
| D2 | 直し後 close（formal / conversational とも `reason=revise`） | **済** |
| D3 | lessons 生成（`lessons_from_critique_kind` → `haiku_lessons.jsonl`） | **済** soft。praise は loosen |
| D4 | 次回発句へ soft 最大 **3** 行（provider → `_haiku_constraint_details` → prompt「参考・強制ではない」） | **済** H5.1 |
| D5 | テスト | **済** `test_haiku_workshop` / constraint lessons |
| D6 | 口答え soft 化・計画書と実装のトーン揃え | **済**（`render_workshop_reply`） |

**編集注意:** lessons を `haiku.py` にベタ書きしない。provider 経由で memory から注入。  
**hard 禁止は道具・読みのみ。** player `forbidden_fragments` は hard に合流しない。

---

## Phase E — catalog / memory の箱寄せ（整理 PR、workshop と分離可）

| 順 | 作業 |
|---|---|
| E1 | `catalog/` 作成、`entry_catalog` を facade に残し topics/plausibility を切り出し |
| E2 | `catalog_readings` → `catalog/readings.py` + re-export |
| E3 | （任意）`memory/` パッケージ化、旧 `memory.py` re-export |

**workshop と同時にやらない。** 通ったあとに単独 PR。

---

## Phase F — mixin ダイエット（後回し）

| 順 | 作業 |
|---|---|
| F1 | `_render_player_chat_reply` → `dialogue/chat_render.py`（service/machine から呼ぶ） |
| F2 | haiku_context を `haiku/context.py` へ移動 + re-export |
| F3 | mixins/haiku.py は「タイミングと emit」だけに |

---

## 1 画面チェックリスト（毎回の PR）

```text
[ ] 新ファイルに実装した
[ ] 旧パスは re-export または削除済み参照なし
[ ] rg で旧モジュール名を検索した
[ ] 関係テストを回した
[ ] haiku mixin に 50 行以上足していない
[ ] Session の workshop を process のどこで触るかコメントした
```

---

## 依存の矢印（実装時に守る）

```text
service
  → haiku.workshop   (pin, close, intent, reply materials)
  → dialogue.chat_policy / player_input
  → memory
  → state_machine (発句は既存)

haiku.workshop
  → memory_types / HaikuEmission（型）
  → しない: state_machine.mixins.*

mixins.haiku
  → 発句後: emission を返すだけ、または service が拾う
  → open_from_emission は service 側推奨（session を持っているのは service）
```

**session を持つのは service** なので、  
`open_from_emission` の呼び出しは **service が haiku 結果を受け取った直後**が一番きれい。

```text
machine.process → haiku_emission
service: if emission: session.haiku_workshop = open_from_emission(...)
         memory.save_agent_haiku(...)
```

---

## おすすめの最初の3コミット／PR

| PR | 内容 | 完了の見え方 |
|---|---|---|
| **PR-1** | Phase A + B1〜B4（dialogue.chat_policy 移動） | import 整理、挙動不変 |
| **PR-2** | Phase C1〜C2（pin open/close） | ログやテストで workshop 状態が見える |
| **PR-3** | Phase C3〜C4（意図＋critique＋材料開示） | 実プレイで「って何?」が句に紐づく |

その後 PR-4 で D（直し・lessons）。

---

## まとめ

| やりたいこと | 順番 |
|---|---|
| 散らかりを止める | A → B（箱＋chat_policy） |
| pin / 忘れない | C1 → C2 |
| やり取り成立 | C3 → C4 |
| 次回に効く | D |
| さらに整理 | E → F |

**ファイル移動の鉄則:** 中身コピー → 旧 re-export → rg → テスト → 参照更新 → 旧削除は最後。
