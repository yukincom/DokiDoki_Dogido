# 発句の内部表現を観測する独立実験

本体と同じ要求を使い、Gemma 4 QATの生成時活性と転用J-lensを比較する。サーバー・音声・記憶には接続しない。感情の同定や本体への接続は未実施。

[実測・限界・モデル変更時の方針](../../docs/2026-10-16-haiku-internal-representations.md)

- `validate_mlx.py`: native計算、層境界、量子化MoEのVJP検査
- `measure.py`: 生成、token対応、全層読出し、重み・レンズ識別
- `jacobian_check.py`: 公式推定量に沿う少数方向の数値診断
- `dogido-rust/examples/haiku_probe_requests.rs`: 既存Rust経路から要求を作り、応答を同じパーサーへ通す入口

入力・重み・レンズを固定したローカル実験用。新モデルを自動選択・downloadしない。公開レンズ、変換済み行列、明示的checkpoint bindingを実験の`reference/`に置く。別モデルでは採取位置と適合性を再検証する。

```sh
./dogido-rust/cargo.sh build --example haiku_probe_requests --locked
"$MLX_PYTHON" dev_tools/haiku_internal_probe/measure.py \
  --model "$MODEL_SNAPSHOT" --output "$EXPERIMENT_DIR/run" --fixture-index 149
```

生成上限・生成方式は既存fixtureを保持する。出力句は採否検証前の初回draft。途中のscene応答も本体の既存パーサーを使う。生成済み結果の再開には要求、seed、token列、重み・レンズidentityの一致が必要。

モデルのテンプレート・生成設定・実行ライブラリ・記録対象のnative実装と採取器もbindingに固定する。初回2件はこの検査拡充前の記録なので現行版では再開せず、新しいrunへ保存する。
