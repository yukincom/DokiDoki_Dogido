# ボイス配信（速度・間・演技）方針

**日付:** 2026-07-28  
**状態:** 方針メモ（Phase 0 は設定で試せる。Phase 1 以降は実装前）  
**きっかけ:** [Issue #13](https://github.com/yukincom/DokiDoki_Dogido/issues/13)  
うみれおんさんアドバイス（ボイスが早い／子ども向けにゆっくりはっきり／繰り返し／演技がかり）と、オーナー追記（文脈別速度・川柳の呼吸・SE idea）。

関連:

- [dialogue-design.md](dialogue-design.md) … 対話モード
- [haiku-architecture.md](haiku-architecture.md) … 発句パイプライン
- [companion-maturity.md](companion-maturity.md) … 完成度の優先軸
- [research/tts-landscape-2026.md](research/tts-landscape-2026.md) … TTS 世代・コミュニティ・適性・権利（調査メモ）
- 実装: `dogido_server/audio.py` · `config.py` · `state_machine/types.py`（`AudioAction`）

---

## 1. ゴール（体感）

| 優先 | 体感 |
|---|---|
| 高 | **川柳に間がある**（「ここで一句」→ 5 → 7 → 5 で呼吸） |
| 高 | **平時はゆっくり、バトルは今くらい**（全部一律に遅くしない） |
| 中 | 子どもにも聞き取れるはっきりさ（速度・区切り） |
| 低〜後 | 繰り返し読み、演技がかり（ブレス・助詞・大げさ抑揚）、雅 SE |

**速度・間・いつ読むかはコード側。** LLM に「ゆっくり読んで」と委ねない。

---

## 2. 現状（実装事実）

| 項目 | 現状 |
|---|---|
| TTS | 既定 VOICEVOX。`DOGIDO_VOICEVOX_SPEED_SCALE` 等（**全体1系統**） |
| 既定 speed | `1.0` |
| `AudioAction` | `layer` / `interrupt` / `text` / `cue_id` / `protect_ms` のみ。**発話単位の speed なし** |
| 川柳読み上げ | 句テキストを **1 本の speech** で一気に再生しやすい |
| 悲鳴・息 | **cue mp3**（`afplay`）。TTS 速度とは独立 |
| キャッシュ | VOICEVOX は speaker・speed・pitch・volume をキーに含む（設定変更で別ファイル） |

だから「`.env` の speed を下げる」だけでも全体の早さは改善できるが、**文脈差・川柳の間**には届かない。

---

## 3. アドバイスの分解

### 3.1 うみれおんさん（#13 本文）

- 全体が少し早い
- 小さめの子ども向けに、**ゆっくりはっきり**語りかけるモードが欲しい
- **繰り返し読み上げ**もあるとよい（何度も言われると納得しやすい）
- 抑揚は激しすぎるくらい・**演技がかり**の方が「物語に来た」感じ
  - 手段の例: ブレス、激しい抑揚、変な区切り、不自然な助詞

### 3.2 オーナー追記（同 issue コメント）

- バトルは今の速さでもよい
- 友好・中立 mob、平時バイオーム反応はもっとゆっくり
- **川柳が特に早い** → 5-7-5 の間に呼吸
- idea: 拍子木 SE →「ここで一句」／ベースからの見どころ一言／句後に琴など

---

## 4. フェーズ計画

### Phase 0 — 耳合わせ（設定のみ・実装不要）

```bash
# .env 例（コミットしない）
DOGIDO_VOICEVOX_SPEED_SCALE=0.85
# 必要なら pitch / volume も
```

- **目的:** 「全体が早い」の確認と、子ども向け下限の感覚を耳で決める
- **限界:** バトル TTS も一緒に遅くなる（cue 悲鳴は別）

### Phase 1 — 本線（小さく効く）

#### 1-A. 発話プロファイル（文脈別スピード）

| プロファイル | 用途 | 目安（要チューニング） |
|---|---|---|
| `battle` | 警告・戦況コール | 現状付近（〜1.0） |
| `peace` | 雑談・友好/中立・バイオーム等 | ゆっくり（0.8〜0.9） |
| `haiku` | 川柳まわり | さらにゆっくり（0.75〜0.85） |

実装の方向:

- `AudioAction` に `speech_profile` または `speed_scale` を足す
- `VoicevoxSpeechBackend` が合成時に上書き（キャッシュキーは既存どおり speed を含む）
- 状態機械が kind / layer からプロファイルを付ける

#### 1-B. 川柳の間（体感インパクト最大）

ねらいの並び:

```text
（任意）拍子木 SE
「ここで一句」
  間
上の句（5）
  間
中の句（7）
  間
下の句（5）
（任意）余韻 SE
```

- 5-7-5 分割は haiku 側に既存ロジックがある前提で **複数 `AudioAction` を順に積む**
- SE は既存 cue 機構 + 音声ファイル
- **バトル割り込みは壊さない**（長い読みの途中でも interrupt 可能のまま）

#### Phase 1 でやらない

| 項目 | 理由 |
|---|---|
| 繰り返し読みモード | いつ／何回／止め方の UX が要る |
| LLM に演技助詞を強要 | ブレとプロンプト肥大。先に間と速度 |
| 画面エフェクト | 別系統（#11 / #15 寄り） |

### Phase 2 — 味付け（余裕が出てから）

- 拍子木・琴など **雅 SE**
- 発句前の短い「見どころ」一言（materials 由来）。順序とレイテンシに注意
- 子ども向け **一括スロープロファイル**（env / 設定）
- 重要助言の **1 回リピート**（同一 text を2回、または「もういっぺん」）
- 演技がかりは **定型カタログ＋限定プロンプト** で小さく

---

## 5. 実装優先の推奨順

```text
1. Phase 0: .env で全体 speed を耳合わせ
2. Phase 1-B: 川柳 5-7-5 分割読み + 間
3. Phase 1-A: peace / battle / haiku 速度差
4. Phase 2: SE・preface・リピート・演技（素材と必要性に応じて）
```

---

## 6. 設計の不変条件

1. **速度・間・発話分割はコード**（audio / 状態機械）。LLM は言い回しのみ  
2. **緊急 cue・ハード割り込みを遅延させない**  
3. VOICEVOX キャッシュは **speed 変更で自動分離**（既存キー設計を維持）  
4. 川柳を複数発話に分けても **protect / interrupt 方針と矛盾させない**  
5. レイテンシ: 分割合成は回数が増える。必要なら後で prewarm / 並列合成

---

## 7. 設定キー（現行）

| 環境変数 | 役割 |
|---|---|
| `DOGIDO_TTS_BACKEND` | `voicevox` / `say` / `noop` |
| `DOGIDO_VOICEVOX_SPEED_SCALE` | 全体話速（既定 1.0） |
| `DOGIDO_VOICEVOX_PITCH_SCALE` | ピッチ |
| `DOGIDO_VOICEVOX_VOLUME_SCALE` | 音量 |
| `DOGIDO_VOICEVOX_SPEAKER` | 話者 ID |
| `DOGIDO_VOICEVOX_CACHE_MAX_MB` | TTS キャッシュ上限（既定 256）。超過分は古い順に削除 |
| `DOGIDO_VOICEVOX_CACHE_MAX_AGE_DAYS` | キャッシュ保持日数（既定 7）。0 以下で年齢削除オフ |
| `DOGIDO_CUE_BACKEND` | 悲鳴等 cue |

TTS キャッシュは `.dogido_tmp/voicevox/cache/`（gitignore）。起動時・新規合成後に prune。  
手動掃除: `rm -rf .dogido_tmp/voicevox/cache/*`

`cue_voice/` の内訳・コールアウト断片方針は §10 と [../cue_voice/README.md](../cue_voice/README.md)。

Phase 1 以降で profile 別キーを足す場合は、この表と `.env.example` を同時更新する。

---

## 8. 受け入れの目安（Phase 1 完了時）

- [ ] 平時の雑談・友好モブ反応が、バトル警告より明らかにゆっくり聞こえる  
- [ ] 川柳が「ここで一句」のあと、句が 5 / 7 / 5 で区切って読まれる  
- [ ] 川柳読みの途中で脅威割り込みが従来どおり効く  
- [ ] speed / profile 変更後に古い VOICEVOX キャッシュを誤用しない  

Phase 0 のみでも「全体が少し遅い」が確認できれば issue の一部は前進。

---

## 9. TTS エンジンについて（本線方針）

- **本線は当面 VOICEVOX。** #13（速度・間）はエンジン差し替えより先。
- 世間の TTS 盛り上がりと「ドギドに最適か」は別。詳細は [research/tts-landscape-2026.md](research/tts-landscape-2026.md)。
- 声優・クローン・各話者の利用規約は技術選定と同等に扱う。同意のないクローンはしない。

## 10. コールアウト音声: パズル連結（方針）

### 10.1 結論

戦況コールアウトは **全文 TTS の都度生成より、事前断片のパズル連結を本線**にする。

```text
[任意] panic cue（悲鳴 mp3）
  → 名称断片（敵対・中立のみ）
  → 体数断片（1〜8体）
  → 定型句（おるで / がおるで 等）
  （方向などは短定型部品 or 短い補助 TTS）
```

| 経路 | 方式 |
|---|---|
| 悲鳴・息 | cue mp3（現状どおり） |
| **戦況サマリー（名前・体数・おるで）** | **断片パズル**（配線済み） |
| **単体視認・長文戦況** | **都度 TTS のまま**（§10.5。キュー全展開しない） |
| 雑談・川柳・workshop | 全文 TTS + 速度プロファイル（#13） |

Minecraft のモブ集合はほぼ閉じているため、名称断片の事前生成は現実的。  
全文パターンを全録音する必要はない。

### 10.2 どのモブ名を持つか

| 含む | カタログ | 例 |
|---|---|---|
| **敵対** | `hostile` | creeper, skeleton, zombie… |
| **中立** | `neutral` | enderman, wolf, piglin… |
| **含めない** | pure **passive（友好）** | cow, sheep, villager, axolotl… |

コード上の集合: `CALLOUT_MOB_VOICE_LABELS` = `threat_mob_labels()`（hostile ∪ neutral）。

以前 `cue_voice/mob/` が約 80 あったのは **友好名まで一括生成していた**ため。  
コールアウト用ではないので友好名 mp3 は削除し、敵対・中立のみ復活した。

### 10.3 アセット配置

| パス | 内容 |
|---|---|
| `cue_voice/mob/{id}.mp3` | 敵対・中立の読み上げ名 |
| `cue_voice/common/counts/{1-8}.mp3` | 「N体」 |
| `cue_voice/common/phrases/*.mp3` | 「おるで」等 |
| `cue_voice/entity_cache_manifest.json` | 一覧・欠けリスト |
| `cue_voice/panic/` | 悲鳴（別系統・使用中） |

生成: `python scripts/generate_entity_voice_cache.py`（友好は生成しない）。  
詳細: [../cue_voice/README.md](../cue_voice/README.md)

### 10.4 実装状態

| 項目 | 状態 |
|---|---|
| 方針 | **確定**（パズル本線） |
| 敵対・中立名称 mp3 | リポに配置（threat 56 種） |
| 体数・定型句 mp3 | リポに配置 |
| **連結再生** | **一部配線済み**（体数サマリー系） |
| 友好名称 mp3 | 置かない |

#### 配線済み（2026-07-29）

- `AudioAction.cue_sequence` + `AudioDispatcher` が断片を順再生
- `CalloutPayload`（text + cue_sequence）
- **複数体／複数種の「〇〇N体…おるで」**（`_hostile_count_summary`）が断片を優先
  - 欠けがあれば全文 TTS にフォールバック
- **うしろ named**（§11）が名前断片 + `ushiro_tail` で配線済み
- 方向付き単体コール・ボス長文・**うしろ classic（志村）** は全文 TTS

#### コード地図

| 部品 | パス |
|---|---|
| モブ名・体数断片 id | `dogido_server/callout_fragments.py` |
| 呼び名断片解決 | `dogido_server/player_name_voice.py` |
| 再生 | `dogido_server/audio.py`（`cue_sequence`） |
| サマリー文言 + 断片 | `mixins/auditory.py` `_hostile_count_summary` |
| うしろ | `mixins/common.py` `_ushiro_callout` |
| 発火 | `mixins/action_builder.py` / `py_tree_policy.py`（`_callout_audio_action`） |

#### 次

- 欠けモブ時のログ監視
- 速度: callout 断片は battle テンポ、平時 speech は slow（#13）
- 記憶の人物プロファイル境界（#20 / [multi-user-tenancy.md](multi-user-tenancy.md)）と call_name 切替 UX
- **単体視認の方向フル部品化はしない**（§10.5）

### 10.5 キューと都度 TTS の線引き（2026-07-29 合意）

**いま全文 TTS（都度生成）の戦況コールは、都度生成のまま残す。**  
方向×助詞×締めを全部キュー部品に広げない。音質（一文としての自然さ・ブツ切り回避）を優先する。

| 方式 | 対象 |
|---|---|
| **キュー／断片パズル** | panic 悲鳴・息；モブ名＋体数＋おるで（サマリー）；うしろ named（名前＋ushiro_tail） |
| **都度 TTS のまま** | 一般単体視認（`{D}！ {M}や！` 等の全バリアント）；特殊ボス長文；classic 志村；中立転・停滞・圧倒の長い文；雑談・川柳・workshop |
| **任意・後から耳で判断** | 「増えたで」1定型だけキュー化する、など **型を絞った**追加のみ |

理由:

- 断片連結は助詞・短い切れで **ブツ切り・韻律の不連続**が出やすい  
- 方向フレーズを `{D}に` / `{D}や` / `{D}！` 全展開するとファイル数が増え、メンテと音質の両方で割に合いにくい  
- 真後ろ近接は **うしろ専用ルート**があり、一般方向の「後ろ」フルキュー必須ではない  
- 既にキュー化したのは **名詞・数・決め台詞の尾**など、切れが少なく繰り返し効くものに限定

方針の一文: **戦況の部品箱は閉じたまま。文としてしゃべる部分は都度 TTS。全部キューがゴールではない。**

---

## 11. 呼び名音声・うしろコール（配線済み）

運用の詳細は [../cue_voice/player_names/README.md](../cue_voice/player_names/README.md)。

### 11.1 うしろは二型（名称モブコールではない）

| 型 | 文言 | 音声 |
|---|---|---|
| **classic** | `志村！うしろ！うしろ〜！`（カタログ定型） | **全文 TTS**（名前スロット非使用） |
| **named** | `{call_name}うしろ！うしろ〜！` | **可能なら** `[名前断片] + ushiro_tail.mp3`。解決失敗時は全文 TTS |

シードで classic / named を選ぶ（`use_classic_ushiro_call`）。

### 11.2 call_name の優先順（ハードコードしない）

| 優先 | 出所 |
|---|---|
| 1 | イベント `meta.call_name` |
| 2 | `DOGIDO_DEFAULT_CALL_NAME`（`.env`） |
| 3 | `player.name`（マイクラ） |
| 4 | フォールバック「プレイヤー」 |

体力警告など呼びかけも **同じ `_player_call_name`** を使う（ログイン名直差しにしない）。

### 11.3 名前断片の解決（player_1 固定禁止）

`player_1.mp3` は **家庭用スロットのファイル名**にすぎない。コードが常に player_1 を鳴らすことはない。

```text
call_name
  → cue_voice/player_names/manifest.json の call_name_to_file
  → player_names/{call_name}.mp3
  → 安全化したファイル名.mp3
  → なし → named は全文 TTS
```

| ファイル | git |
|---|---|
| `player_N.mp3` | **対象外** |
| `manifest.json` | **対象外**（家庭用マッピング） |
| `player_1_example.mp3` / `ushiro_tail.mp3` / `manifest.example.json` | リポ |

切替例: `.env` の `DOGIDO_DEFAULT_CALL_NAME=プレイヤーツー` と manifest の対応付けで `player_2` + tail が鳴る。  
別の人に変えるときは **call_name と manifest** を更新（コードの定数変更は不要）。

### 11.4 世帯・記憶との関係

- 名前音声は call_name キーで複数持てる  
- 川柳・lesson の人物分離は **まだ単一空間** → [#20](https://github.com/yukincom/DokiDoki_Dogido/issues/20) / [multi-user-tenancy.md](multi-user-tenancy.md)  
- 同時に複数人と会話する想定はない（交代プレイのみ）

## 12. 状態ログ

| 日付 | 内容 |
|---|---|
| 2026-07-28 | Issue #13 を踏まえ方針を文書化。実装は未着手。 |
| 2026-07-28 | TTS 地図・権利の注意を research メモに追加。本線は VOICEVOX 維持。 |
| 2026-07-29 | VOICEVOX キャッシュに max MB / max age の自動 prune。 |
| 2026-07-29 | コールアウトはパズル連結を本線と明記。友好名 mp3 を外し敵対・中立のみ復活。 |
| 2026-07-29 | 体数サマリー系コールアウトの断片再生を配線。欠け時は TTS フォールバック。 |
| 2026-07-29 | うしろ named を call_name→断片解決で配線。player_1 固定禁止を docs に明記。 |
| 2026-07-29 | §10.5: 都度 TTS の戦況コールは都度のまま残す。方向フル部品化はしない（音質優先）。 |
