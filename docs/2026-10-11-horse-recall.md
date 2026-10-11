# 所有馬の呼び戻し（2026-10-11）

状態: native API方式を採用し実装。関連単体・模擬HTTPとFabricの自動検証が通過。Minecraftでの実移動・音・見た目は未確認。

## 会話からの流れ

1. 通常会話モデルが、今回の本人による「今ここへ馬を呼び寄せる依頼」を判断する。この段階へ所有馬一覧は渡さない。名前の完全一致や肯定操作語の列挙で入口を絞らない。
2. 依頼の原文全文とconfidenceをRustが検査した場合だけ、Minecraftが送った現在の候補一覧をモデルへ渡す。
3. モデルが名前の表記ゆれ・会話・最後の騎乗から一頭の短いcandidate IDを選ぶ。候補が曖昧なら質問する。登録名の辞書は持たない。
4. Rustが元の候補表のUUIDを取り出し、現在のsession・turn・処理世代・個体状態と照合して、既存assist配送へ5秒の命令を積む。
5. Fabricの統合サーバーが実状態と着地点を再検査し、同じUUIDの馬を移動する。移動結果が届くまでは成功として扱わない。

平常時の通常会話は一回、呼び寄せ依頼は最大二回のLLM呼出し。戦闘中は既存の戦況・知識受付の後、同じ二段契約を操作支援の一件枠で実行する。馬以外と判断した入力は生成本文を発話せず、再判定しない印を付けて既存の戦闘中入力処理へ戻す。各段階の再生成・別方式へのfallbackは行わない。根拠の省略や過去発言の混入、confidence 0.9未満、不完全な操作出力は実行しない。名前・語句の肯定リストを実行条件にはしない。否定・引用・伝聞・予約・単なる名前の呼びかけ・場所や能力の質問は会話で扱う。

## 対象と実行条件

初版は自分のシングルプレイの通常の馬（HorseEntity）に限る。本人が飼い慣らして所有し、同じ次元で読み込まれている生存個体が対象。乗員あり・リードありは呼べない。ロバ・ラバ等への拡張や、未ロード個体を探すチャンク読込は行わない。

観測の所有馬一覧は最大32頭。上限を超えると部分一覧を使わず全体を利用不可にする。表示名は現在の名札、個体の正はUUID。改名・所有者変更・乗車・死亡・ワールド変更が生成待ちに起きた場合、古い選択を実行しない。snapshotは過去3秒、時計差による未来1秒までを許容する。

逃走のため、敵・警告・被弾・panic中も本人の明示依頼を受け付ける。戦闘中の判定と命令発行は警告音声の直列枠を待たず、通常会話の取消世代とも分離し、既存assistの入力世代・取消・session instanceで照合する。ゲーム一時停止中・観測が古い場合は実行しない。通常雑談・workshop・川柳の戦闘抑制は維持する。生成中に新しい入力が入った場合は古い結果を破棄する。すでに発行した命令は後の発言だけでは取り消さず、同じIDの再配送・5秒の期限・Fabricの再検査で管理する。

着地点はプレイヤー付近2〜5ブロックを調べ、2×2の支持面と3ブロックの空間、危険ブロック・液体・他のentity・ワールド境界・高さ・読込済み範囲を検査する。準備10 tick後にも所有・拘束・プレイヤー位置・足場を検査し、成立しなければ移動しない。

## 出現演出

準備光の後に同じ馬を移動し、前脚を上げる状態にする。クライアントで到着と立ち上がりの進行を確認した同じtickに、馬のいななきと中心が白・周囲が黄色の粒子を出す。粒子は周囲へ散り、明滅しながら消える。外部素材の追加はなく、vanillaのglitter spriteを使う。

移動ACKは移動成功の証拠であり、音や描画の観測証拠ではない。演出待ちがタイムアウトしても二度移動しない。実機での色・タイミング・馬体との重なりは別途確認が必要。

## 通信契約

専用endpointは設けず、既存の登録・game-events・commands・command_results・ACKを使う。

- 登録の実行権限: `execution_capabilities: ["integrated_server.horse.recall.v1"]`
- 観測: `owned_horses` は任意。`status_snapshot` で省略された場合は一覧を撤去する。馬一覧を持たない戦況・聴覚等のイベントでは直前の一覧を元の観測時刻のまま保持し、鮮度を延長しない。次元変更でも破棄する。
- 一覧: `schema_version:1, status:available|unavailable, world_id, revision, observed_at, player_uuid, dimension, recent_mount_id, candidates`
- 各候補: `entity_id, custom_name, tamed, owned_by_player, alive, dimension, has_passengers, leashed`
- 命令: `type:recall_horse, command_id, issued_at, expires_at, world_id, player_uuid, dimension, entity_id`
- 結果: `command_type:recall_horse, command_id, status, executed_at, detail_code, entity_id, world_id`。識別子はトップレベルに置く。

`world_id` は接続・ClientWorldが変わると新しくする。同じ次元名の別世界へ命令を持ち込まない。revisionが増えても対象情報が一致すればよい。未知IDの結果はACKするが成功の証拠にせず、正しい命令・種類・個体・世界・期限が一致した成功だけを一度記録する。

実行と再配送の期限は発行から5秒。結果の受信にはさらに5秒の猶予を設け、期限内に動いた馬の結果が次の観測で届いても成功を確認できるようにする。猶予中の新しい呼び戻しは受け付けず、実行時刻が5秒の期限外なら成功扱いにしない。受信猶予を過ぎた結果は復活させず、「確認できなかった」と記録・案内する。

## 配置と検証

- `dogido-rust/src/assist/horse_recall/`: 依頼・候補選択の契約、原文/個体検証、命令の待ち行列。
- `dogido-rust/src/dialogue/horse_recall_runtime.rs`: 馬一覧の保持、通常会話からの呼出し、最新状態への適用、実行結果の会話への接続。
- `dogido-rust/src/dialogue/horse_recall_runtime/assist.rs`: 戦闘・警告中の限定操作。通常会話と同じnative二段契約と命令queueを使う。
- `adapter/minecraft-fabric/.../horse_recall/`: 所有馬観測、実移動、着地点、演出。
- `dogido-rust/scripts/check_horse_recall.py`: 実HTTPと模擬LLM/TTSによる接続・候補の段階的公開・再配送・ACK・取消・改名検査。

最終コードでRust全656テスト（33 target）と `clippy --all-targets --locked -- -D warnings` が通過。模擬HTTPは7項目が通過し、期限内に実行・期限後に受信した成功を失敗へ変えないことも確認した。Fabricは `./gradlew test build --offline --no-daemon` で133テストとjarのビルドが通過した。これらは実Minecraftでの移動・描画・音声を検証するものではない。

書類側mainへの統合後もFabricの133テストとビルド、Rustの `build --release --locked`、`launch_dialogue.py --settings-dir . --check` が通過。起動用バイナリとMOD jarを元のプロジェクトに用意した。

2026-10-11、ユーザーの実機テスト用の依頼で、通常のFabric起動構成の `mods/dogido-fabric-client-0.1.0.jar` へ配備した。ビルド成果物とのSHA-256一致（`b6854a1bf3f5a5c121441a02264ad107b491a2cb7687c0692c3dfee1b77bcde6`）を確認。旧jarと配備記録は `.dogido_tools/checkpoints/2026-10-11-horse-recall-install/` に退避。配備時点ではMinecraftと旧ビルドのRust本体が稼働していたため、反映にはそれぞれ再起動が必要。実機での馬の移動・画面・音は未確認。

比較結果と採用方式は [API / JSONの適性](2026-10-11-api-json-suitability.md) を参照。実モデル比較は選択結果までを検証し、Minecraftの馬は動かさない。

## 2026-10-12 実機ログからの接続修正

最初の `buchiをここに呼んで` は選択confidenceが0で棄却され、移動命令は発行されていなかった。後続の入力は戦況制限で通常会話へ流れ、未実行なのに呼ぶ旨の本文を返した。ユーザーが呼びたい名札は `pochi` と確認したが、実際のMinecraft候補との照合は未実施。名前の固定登録は追加しない。

馬一覧を持たない完全視認イベントで直前の一覧が消える接続を修正した。既存の対話snapshotへ、一覧・元の観測時刻・停止理由の診断表示を追加した。候補が古い・無い・ゲームが停止中でも依頼を普通の雑談へ落とさず、呼んでいない理由を返す。選択段階の通信失敗も検証済みの依頼として保持し、通常雑談へ戻さない。

「敵に囲まれている時ほど馬が欲しい」という用途に合わせ、非戦闘制限を撤去した。着地点の足場・空間・液体・衝突と、所有・生存・ロード・拘束の検査は維持する。平常時に開始した会話処理が途中で戦闘へ切り替わり取消された場合は、再依頼が必要。戦闘中に受け付けた操作支援は、その後の警告・被弾だけでは取り消さない。

ユーザー指示によりローカルLLMへの実リクエストと稼働本体の再起動は保留し、今回の確認は単体・模擬HTTP・ビルドに限る。

修正後はRust全659テスト（33 target）、`clippy --all-targets --locked -- -D warnings`、リリースビルド、模擬HTTPの13項目が通過。HTTP検査は完成したリリースバイナリを使用し、警告と被弾中の発行、次入力による取消、選択段階の通信切断も確認した。既定のpyenv shim経由では既存の辞書補助の期限テストがPIDファイル未作成で失敗したため、プロジェクトの `dogido-llm/bin/python` を `DOGIDO_TEST_PYTHON` に指定して単独・全体を再実行し、通過を確認した。関連ソース以外の修正は行っていない。

本体バイナリのSHA-256は `1988761f119fa7e0ca761c99b71b83b96d2a8108e7032beffb16105eef3880ea`。修正前の実機ログ・模擬検証ログ・manifestは `.dogido_tools/checkpoints/2026-10-12-horse-recall-live-fix/` に保持した。今回はFabricを変更していないためMODの再配置は不要。本体の再起動後に `pochiをここに呼んで` と依頼し、実移動・音・表示を確認する。
