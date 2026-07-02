# `py_trees` 統合メモ

このプロジェクトでは、`py_trees` を **状態遷移の置き換え** ではなく、**出力優先制御の policy layer** として使う。

## 使い方

- `DogidoStateMachine`
  - 内部記憶
  - 派生シグナル計算
  - `normal / alert / panic / suppressed_panic / aftermath` の状態遷移
- `PyTreeActionPolicy`
  - 各状態で何を喋るか
  - どの cue / callout / speech を優先するか
  - `panic > suppressed_panic > alert > aftermath > ambient`

## なぜ全部置き換えないか

- `dogido-server` は event-driven で、1イベントごとに即時判定したい
- `panic_cue` のクールダウンや `うるさい` 抑制は、状態機械の方が明快
- 一方で「今どの枝を通すか」は behavior tree の方が可視化しやすい

## LLM との統合ポイント

LLM は leaf node に差し込める。

現行実装:

- `EmitAmbientMobActions`
  - 昼モブ雑談を LLM 生成へ差し替え
- `EmitAftermathActions`
  - 戦闘後の余韻コメントを LLM 生成へ差し替え
- `EmitDeathActions`
  - 死因別コメントを LLM 生成へ差し替え

ただし、以下は LLM に渡さない方がよい。

- panic 開始判定
- cue 割り込み
- `うるさい` 抑制
- 近距離脅威の最優先処理

## 現在の tree 構成

- `Death`
- `Panic`
- `SuppressedPanic`
- `Alert`
- `Aftermath`
- `AmbientMob`

根は `Selector(memory=False)` で、上から優先順に評価する。

## 設定

`.env` または環境変数:

```text
DOGIDO_DECISION_POLICY=py_trees
DOGIDO_LLM_ENABLED=true
DOGIDO_LLM_BACKEND=mlx
```

旧ロジックに戻したい場合:

```text
DOGIDO_DECISION_POLICY=legacy
```
