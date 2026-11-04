# mainの現行仕様に合わせた自動試験の更新

2026-11-05。通常会話・環境反応の視認表示、音源IDの保持、試験保存先の隔離に合わせてテストを更新した。本体の判断・生成プロンプト・保存仕様は変更していない。

## 更新した期待と試験条件

- 視認は「いま見えとるもん」「さっきまで見えとったもん」の表示行を検査し、残る資料は末尾の完全なJSONとして読む。種類・数量・村人の職業内訳、聴覚の時刻・確度、履歴の内容と順序の期待を維持した。
- 非交戦ゾンビのfixtureは実際の`passive_mobs`経路に揃えた。交戦相手の配列と周辺集計へ同じ個体を二重登録しない。
- 旧移植fixtureは再生成せず、新しい`source_id`を旧項目との比較から分離した。現在の音源IDと聴取時刻は別の保持試験で照合する。
- 相談終了後の要約試験は、自動発句間隔0秒による再発句との競合を避ける。相談32往復のうち古い22往復を要約し、直近10往復を保持する期待は変えていない。
- 独自にプロセスを起動する接続・音声入力・川柳worker・テキスト相談室の4本も`isolated_storage`を使用する。親の保存設定を除き、HOME・作業先・再起動後の保存先も試験用一時領域へ向ける。
- CIのPythonジョブ内でも、保存隔離のHTTP試験に必要なdebug本体をビルドする。別ジョブのビルド成果物は共有されないため、この準備を追加した。

## ローカルでの結果

| 範囲 | 結果 |
|---|---|
| Rust全target | 1,181件成功、明示実行用2件は既定どおり除外 |
| Python本体・共通補助 | 323件と42 subtest成功、条件付き19件はskip |
| 本体の模擬HTTP等 | 実行可能な59入口すべて成功 |
| 身体Mod / Fabric adapter | 176件 / 384件成功、build成功 |
| 本棚 / 開発ツールのJavaScript | 107件 / 6件成功 |
| 独立ラボ・署名照合のPython | 115件成功。MLXの小配列試験5件を含む |
| macOS管理アプリ | 単体57項目＋模擬統合80項目成功 |

Rustは`cargo.sh test --all-targets --locked --offline`、Pythonは`pytest -q tests dogido-rust/tests/python/test_*.py`を使用。Javaは身体側・adapter側をそれぞれ検査した。結合試験は模擬モデル・模擬音声と試験ごとの一時保存先を使用し、adapter生成の敵外周wire fixtureもRust側のHTTP試験へ渡した。

独立ラボは同名モジュールを混ぜないよう各ディレクトリで個別収集した。画像ラボはPillow入りの既存環境、管理アプリは一時領域に作るmock serverで検査した。debug本体・検査用example・release本体のビルドも成功。

## この結果に含めないもの

- Rustの全文プロンプト出力、実VOICEVOX／スピーカー確認。
- pytestの明示実行用SSH・実Qwen・Bonjour・CPU学習、ipywidgets、専用Minecraft DB、Whisper C++の条件付き試験。
- 実モデルを使う`check_dialogue_live`、`check_horse_recall_model`、`check_workshop_live`、`check_workshop_edit_live`、`check_workshop_revision_live`。`check_workshop_fixture`は補助モジュールで実行入口を持たない。
- Minecraft実機、実マイク・音声、実BERT／生成モデルの理解・応答品質。

他作業の教材CSV・資料・画像研究の未コミット変更は、この更新に含めない。
