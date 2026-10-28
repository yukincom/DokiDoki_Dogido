パート2は発言の時刻・種類・返答先の記録と復元を確認します。各ケースの狙いは `../history_metadata.rs` のテスト直前にあります。

実行（リポジトリ直下）:

```sh
./dogido-rust/cargo.sh test --lib --locked acceptance_history_metadata -- --test-threads=1
```

音声・画面の完了通知を模擬します。実機・実モデルの品質は別途確認が必要です。
