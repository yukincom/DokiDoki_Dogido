# cue_voice

実行時に再生する音声アセット。

## ディレクトリ

| パス | 用途 | 状態 |
|---|---|---|
| `panic/` | 悲鳴・息など戦闘 cue | **使用中**（afplay） |
| `aftermath.mp3` | 戦闘後など | **使用中** |
| `mob/` | **敵対・中立**の名称断片 | 素材あり・**体数サマリーで連結再生** |
| `common/counts/` | `1体`〜`8体` | 同上 |
| `common/phrases/` | `おるで` / `がおるで` | 同上 |
| `entity_cache_manifest.json` | 上記断片の一覧 | メタデータ |
| `player_names/` | 呼び名断片（うしろ named） | call_name→manifest で解決。`player_N` は gitignore。`ushiro_tail` / example はリポ |

## 方針（コールアウト）

- **全部をキューにしない。** 名詞・数・決め台詞の尾など、切れが少ないものだけ断片化  
- 断片パズル: 名称 + 体数 + おるで（サマリー）、うしろ named（名前 + ushiro_tail）  
- **単体視認（方向＋モブの各種言い回し）・特殊長文は都度 TTS のまま**（音質優先）  
- **友好（pure passive）の名称 mp3 は置かない**  
- 名称セットはカタログの **hostile + neutral**  
- 雑談・川柳・workshop は全文 TTS  
- 詳細: [docs/voice-delivery-plan.md](../docs/voice-delivery-plan.md) §10.5

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
