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

## Colabでの事前検証

`colab_probe.py` は同じQAT重みのMac/CUDA比較専用。32tokenの全層残差・末尾logits・expert集合と、128tokenのJacobian先頭32座標を採る。本学習やモデル重み更新は行わない。`qat_jacobian.py` の既存計算を共有する。

`build_colab_bundle.py` の `prepare` → Mac比較元の採取 → `package` で、`qat_colab_check.ipynb` と `qat_colab_support.zip` を作る。配布先は `.dogido_tools/experiments/2026-10-17-qat-jspace/colab/`。Colabではnotebookを開き、最初のセルでsupport ZIPをアップロードする。重みは固定revisionを取得し、全ファイルのhashを検査する。

Mac側の実行とnotebookの構文・ZIP整合性を確認済み。CUDA上の実行は未確認。最後のセルで `qat_colab_result.zip` を取得し、数値差・VRAM使用量・速度を確認してから学習方法を決める。単一入力の速度外挿を本学習の所要時間保証とせず、互換性の検査をレンズの独立精度評価と混同しない。
