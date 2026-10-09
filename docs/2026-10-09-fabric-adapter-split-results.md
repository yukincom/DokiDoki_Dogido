# 2026-10-09 Fabricアダプター分割 実施記録

[計画](2026-10-08-fabric-adapter-split-plan.md)のP1〜P17の結果をここへ追記する。

ユーザーの追加指示に従い、挙動を変えない分割は確認待ちせず進める。途中は変更範囲の検証だけを行い、全体テスト・通しビルドはP17で行う。新機能・機能変更を入れる場合だけユーザーのコード確認を先に挟む。

## P1 — 分割前の基準

[基準記録](2026-10-09-fabric-adapter-split-p1-baseline.md)と[manifest](2026-10-09-fabric-adapter-split-p1-baseline.json)へ78フィールド、reset、tick順、8イベントを記録。81ファイルと2削除を復帰資料へ保全した。READMEの旧匂い説明を修正。製品コードは変更していない。

| 検証コマンド | 結果 |
|---|---|
| Fabric: `./gradlew --offline test --tests dogido.fabric.SmellPolicyTest --tests dogido.fabric.ZombieScentPolicyTest` | 22件成功、製品・テストコンパイル成功 |
| Rust: `./cargo.sh test --lib --locked --offline smell` | 14件成功 |
| Rust: `./cargo.sh test --lib --locked --offline rain_after` | 7件成功 |
| Rust: `./cargo.sh test --lib --locked --offline strength` | 8件成功 |

Rustのフィルターは一部重複する。全体テスト、通しbuild、Minecraft、実TTS、実モデルはこの段階では実施していない。

復帰点はこの基準記録を含むGit checkpointと、manifestに記したローカルsource archive。既存の他作業の未コミット変更はまとめてcommitしない。
