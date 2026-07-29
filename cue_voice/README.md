# cue_voice

実行時に再生する音声アセット。

## ディレクトリ

| パス | 用途 | 状態 |
|---|---|---|
| `panic/` | 悲鳴・息など戦闘 cue | **使用中**（afplay） |
| `aftermath.mp3` | 戦闘後など | **使用中** |
| `mob/` | **敵対・中立**の名称断片 | 素材あり・**連結再生は未配線** |
| `common/counts/` | `1体`〜`8体` | 素材あり・未配線 |
| `common/phrases/` | `おるで` / `がおるで` | 素材あり・未配線 |
| `entity_cache_manifest.json` | 上記断片の一覧 | メタデータ |

## 方針（コールアウト）

- 戦況コールアウトは **全文 TTS 都度生成より、パズル連結を本線**にする  
  （名称 + 体数 + 定型句。必要なら方向部品も）
- **友好（pure passive）の名称 mp3 は置かない**（牛・羊・村民など）
- 名称セットはカタログの **hostile + neutral**（`CALLOUT_MOB_VOICE_LABELS` / `threat_mob_labels`）
- 雑談・川柳・workshop はこれまでどおり **全文 TTS**

## 生成

```bash
# 敵対・中立のみ生成（友好は出さない）
python scripts/generate_entity_voice_cache.py
python scripts/generate_entity_voice_cache.py --overwrite
python scripts/generate_entity_voice_cache.py --only creeper --only zombie
```

欠けている脅威モブ名がある場合は上で足す（manifest の `missing_mob_clips` を参照）。

## 置かないもの

- 友好モブ名の断片
- VOICEVOX 全文キャッシュ（`.dogido_tmp/voicevox/cache/`。gitignore・自動 prune）
