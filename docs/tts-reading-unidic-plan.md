# TTS 読み補正 — UniDic / 形態素解析方針

**日付:** 2026-07-29  
**状態:** Phase 1–2 **実装済み**（optional UniDic + 例外表）。Phase 3 実測・Phase 4 既定オン方針は運用しながら。  
**きっかけ:** VOICEVOX が「朝」を「ちょう」と読むなど、自由文 TTS の誤読。自前置換の都度追加は非効率。

関連:

- [voice-delivery-plan.md](voice-delivery-plan.md) §12（現状の薄い辞書）
- 実装: `dogido_server/tts_reading.py`
- 川柳ラベル読み: `catalog_readings.py`（本方針とは別系統）

---

## 1. 結論（いまの合意）

| 項目 | 方針 |
|---|---|
| 自力で誤読語を都度追加し続ける | **やらない**（スケールしない） |
| 第一候補 | **MeCab + UniDic**（語種・読みが TTS 向き）→ **fugashi + unidic-lite で実装** |
| 第二候補 | Sudachi + SudachiDict（導入のしやすさ・現代語） |
| 第三 | Open JTalk 標準辞書系（軽量・組み込み寄り。サーバー本線ではない） |
| 載せる場所 | **dogido_server の VOICEVOX 直前**のみ |
| ログ / `action.text` | **漢字交じりのまま**（表示・デバッグ用） |
| 自前 `tts_reading.py` | **消さない**。UniDic のあと **例外・残差**として残す（優先読み表も同ファイル） |
| 依存 | **optional** `pip install -e ".[tts-reading]"` |

---

## 2. 問題の整理

```text
LLM → 「朝から元気でええねん。」（自然な日本語）
     → VOICEVOX が「チョウから…」と音読み
```

- 川柳用 `catalog_readings` は **カタログラベル**向け。ambient 等の自由文は対象外  
- プロンプトで「訓読みはひらがな」は **ときどきしか通らない**  
- 手書き置換表は効くが、語が増えるほど非効率  

目指す分担:

```text
LLM: 普通の漢字交じり日本語を生成（口調・キャラに集中）
外部辞書 + 軽いロジック: 読みを安全側に寄せてから合成
```

---

## 3. 候補辞書（ライセンス・用途）

いずれも **商用利用可能な緩いライセンス**が前提（導入時に公式表記を再確認し、README Acknowledgments に記載）。

### 3.1 UniDic（第一候補・採用中）

- 開発: 国立国語研究所  
- 実装パッケージ: `fugashi` + `unidic-lite`（UniDic 2.1.2 系、ディスク約 250MB）  
- ライセンス: UniDic は GPL / LGPL / BSD のトリプルライセンス。fugashi は MIT+BSD。詳細はパッケージ同梱 `LICENSE.unidic`  
- 強み: 読み・語種（和／漢／外／混／固 等）  

### 3.2 SudachiDict（第二候補）

- ライセンス: Apache 2.0  
- 関西弁・口語が崩れる場合の比較用に残す（未実装）

### 3.3 Open JTalk 標準辞書（NAIST-jdic ベース）

- 本線ではない（クライアント完結が要る将来用）

---

## 4. ドギドへの組み込み（実装）

### 4.1 パイプライン

```text
LLM 出力（ログ・UI 用。漢字交じり）
        │
        ▼
prepare_text_for_tts()
        │
        ├─ (1) UniDic 経路（optional・engine≠off）
        │      トークン単位でひらがな化
        │      - 優先読み表（一日→いちにち 等）
        │      - 語種が和/混の内容語（漢・固は原則触らない）
        │
        └─ (2) tts_reading 例外表
               残差・辞書無し時の本線・複合フレーズ
               （朝鮮を朝より先に置き単純 replace の誤爆を防ぐ）
        │
        ▼
VOICEVOX audio_query / synthesis
```

- **失敗時:** UniDic 初期化/解析失敗でも落ちず、例外表結果（または原文）で合成  
- **対象:** VOICEVOX 経由の全 TTS  
- **対象外:** cue mp3・断片パズル  

### 4.2 置換ロジック（Phase B 寄りの選択）

| 段階 | 内容 | 状態 |
|---|---|---|
| B | 語種が **和 / 混** の内容語で漢字を含むものだけひらがな化 | **実装** |
| D | 例外表 + トークン優先読み（一日・明日など） | **実装** |
| A | 読みを VOICEVOX の別 API に渡す | 未実施（表記ひらがなで足りる） |
| C | 混種結合の特別処理 | 語種「混」は B に含む。結合単位の追加ヒューリスティックは未 |

「全漢字の機械的ひらがな化」はしない。

### 4.3 依存の置き方

```bash
pip install -e ".[tts-reading]"
# 中身: fugashi[unidic-lite]
```

- 必須依存にしない  
- 設定: `DOGIDO_TTS_READING_ENGINE=auto|unidic|off`（Settings: `tts_reading_engine`）  
  - `auto` … UniDic があれば使う（既定）  
  - `unidic` … 試す（失敗時は例外表のみ）  
  - `off` … 例外表のみ  

### 4.4 性能

- 解析器は **プロセス内シングルトン**（`tts_reading._get_unidic_tagger`）  
- Phase 3 で ambient 短文の ms を実測予定  

---

## 5. 現状（例外表）

| 項目 | 内容 |
|---|---|
| ファイル | `dogido_server/tts_reading.py` |
| 辞書の場所 | `_TTS_HIRAGANA_REPLACEMENTS`（外部 JSON ではない） |
| 処理 | UniDic（任意）→ 単純 `str.replace`（長い語優先） |
| 例 | 朝から→あさから、草地→くさち、一日→いちにち 等 |
| 呼び出し | `VoicevoxSpeechBackend` 合成直前（`engine=settings.tts_reading_engine`） |

---

## 6. 実装フェーズ

| Phase | 内容 | 状態 |
|---|---|---|
| **0** | 本 docs + 現状薄い辞書 | **済** |
| **1** | MeCab+UniDic の optional 導入 | **済**（`[tts-reading]` / fugashi+unidic-lite） |
| **2** | `prepare_text_for_tts` に解析パス接続 | **済**（off 時は従来どおり） |
| **3** | ambient 実ログ数十件で A/B（誤読・違和感・ms） | 未 |
| **4** | 既定オン可否の運用確認・Acknowledgments | **一部済**（auto 既定・README 追記）。実機長期確認は未 |

Sudachi は Phase 3 で「関西弁・口語が崩れる」場合の比較用に残す。

---

## 7. ライセンス・クレジット

- UniDic: GPL / LGPL / BSD（トリプル）。`unidic-lite` 同梱表記に従う  
- `README.md` Acknowledgments に記載  
- 拡張・非公式辞書を混ぜない  

本プロジェクト本体は MIT。依存辞書の条件は README で明示する。

---

## 8. やらないこと

- Fabric クライアント内に MeCab を必須化  
- LLM プロンプトだけで音訓を完結させる  
- 全漢字の機械的ひらがな化  
- 自前で大規模音訓 DB をゼロから構築  
- 解析必須にしない（辞書無し環境を壊さない）  

---

## 9. 状態ログ

| 日付 | 内容 |
|---|---|
| 2026-07-29 | 方針文書化。第一候補 UniDic。実装は未着手（薄い tts_reading のみ稼働）。 |
| 2026-07-29 | Phase 1–2 実装。`prepare_text_for_tts` に例外表→UniDic(和/混) 補完。`[tts-reading]` optional。`DOGIDO_TTS_READING_ENGINE`。 |
