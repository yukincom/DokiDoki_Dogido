# player_names — 呼び名（プレイヤー名）断片

うしろコール **named** 用: `[名前断片] + [ushiro_tail]`。

**状態:** 配線済み（`dogido_server/player_name_voice.py` + `_ushiro_callout`）。  
方針の正本: [docs/voice-delivery-plan.md](../../docs/voice-delivery-plan.md) §11。

## 解決のしかた（ハードコードしない）

実行時は **いまの call_name** からファイルを探す:

1. `manifest.json` の `call_name_to_file`（推奨）
2. `player_names/{call_name}.mp3`
3. 安全化したファイル名 `.mp3`

`player_1.mp3` は **スロット用のファイル名**であり、コードが常に player_1 を鳴らすわけではない。  
別の人に変わったら `manifest.json` のマッピングか `meta.call_name` / `DOGIDO_DEFAULT_CALL_NAME` を変えればよい。

| 優先 | 呼び名の出所 |
|---|---|
| 1 | イベント `meta.call_name` |
| 2 | `DOGIDO_DEFAULT_CALL_NAME`（`.env`） |
| 3 | `player.name`（マイクラ） |
| 4 | フォールバック「プレイヤー」 |

## ファイル

| ファイル | git | 内容 |
|---|---|---|
| `player_1.mp3` / `player_2.mp3` … | **対象外** | 家庭用スロット実体 |
| `player_1_example.mp3` | 含む | サンプル「プレイヤーワン」 |
| `ushiro_tail.mp3` | 含む | 「うしろ！うしろ〜！」定型 |
| `manifest.json` | **対象外** | call_name → ファイル |
| `manifest.example.json` | 含む | マッピング例 |
| `README.md` | 含む | この説明 |

## manifest 例

`manifest.example.json` をコピーして `manifest.json` にする:

```json
{
  "version": 1,
  "call_name_to_file": {
    "プレイヤーワン": "player_1.mp3",
    "プレイヤーツー": "player_2.mp3"
  }
}
```

`.env` で `DOGIDO_DEFAULT_CALL_NAME=プレイヤーツー` にすれば、named うしろは `player_2.mp3` + `ushiro_tail.mp3` になる（manifest が対応している場合）。

## classic バリアント

稀に出る `志村！うしろ！うしろ〜！` は **カタログ定型**で、名前スロットを使わない（全文 TTS）。

## 配線コード

- 解決: `dogido_server/player_name_voice.py`
- 発火: `_ushiro_callout` → `CalloutPayload` + `cue_sequence`
