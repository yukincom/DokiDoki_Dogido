# player_names — 呼び名（プレイヤー名）断片

うしろコール等で使う **「誰を呼ぶか」** の短い名前音声。

## ディレクトリ名

`cue_voice/player_names/`  
（別名候補だった `player_name` より、複数スロットがあることが伝わる複数形）

## ファイル規約

| ファイル | git | 内容の例 |
|---|---|---|
| `player_1.mp3` | **対象外** | 家庭用スロット1（例: プレイヤーワン） |
| `player_2.mp3` | **対象外** | 家庭用スロット2（例: プレイヤーツー） |
| `player_1_example.mp3` | **リポに含める** | サンプル。「プレイヤーワン」 |
| `player_2_example.mp3` | 作らない | — |

- 実運用の `player_N.mp3` は **gitignore**（個人名・家庭差をコミットしない）
- サンプルは `*_example.mp3` のみリポに置く
- 将来プロファイル id に寄せるときは、同じディレクトリに  
  `{profile_id}.mp3` や manifest を足す想定（[docs/multi-user-tenancy.md](../../docs/multi-user-tenancy.md)）

## 生成例（ローカル）

VOICEVOX 起動後:

```bash
# 例: player_1 を「プレイヤーワン」で上書き生成するスクリプトは
# scripts/ に後でまとめてもよい。現状は手動 / エージェント生成で可。
```

配線（うしろ named = 名前断片 + 「うしろ！うしろ〜！」）は未実装。