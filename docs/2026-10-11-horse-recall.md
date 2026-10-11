# 所有馬の呼び戻し（2026-10-11）

状態: native API方式を採用し実装。関連単体・模擬HTTPとFabricの自動検証が通過。Minecraftでの実移動・音・見た目は未確認。

## 会話からの流れ

1. 通常会話モデルが、今回の本人による「今ここへ馬を呼び寄せる依頼」を判断する。この段階へ所有馬一覧は渡さない。名前の完全一致や肯定操作語の列挙で入口を絞らない。
2. 依頼の原文全文とconfidenceをRustが検査した場合だけ、Minecraftが送った現在の候補一覧をモデルへ渡す。
3. モデルが名前の表記ゆれ・会話・最後の騎乗から一頭の短いcandidate IDを選ぶ。候補が曖昧なら質問する。登録名の辞書は持たない。
4. Rustが元の候補表のUUIDを取り出し、現在のsession・turn・処理世代・個体状態と照合して、既存assist配送へ5秒の命令を積む。
5. Fabricの統合サーバーが実状態と着地点を再検査し、同じUUIDの馬を移動する。移動結果が届くまでは成功として扱わない。

通常会話は一回、呼び寄せ依頼は最大二回のLLM呼出し。各段階の再生成・別方式へのfallbackは行わない。根拠の省略や過去発言の混入、confidence 0.9未満、不完全な操作出力は実行しない。名前・語句の肯定リストを実行条件にはしない。否定・引用・伝聞・予約・単なる名前の呼びかけ・場所や能力の質問は会話で扱う。

## 対象と実行条件

初版は自分のシングルプレイの通常の馬（HorseEntity）に限る。本人が飼い慣らして所有し、同じ次元で読み込まれている生存個体が対象。乗員あり・リードありは呼べない。ロバ・ラバ等への拡張や、未ロード個体を探すチャンク読込は行わない。

観測の所有馬一覧は最大32頭。上限を超えると部分一覧を使わず全体を利用不可にする。表示名は現在の名札、個体の正はUUID。改名・所有者変更・乗車・死亡・ワールド変更が生成待ちに起きた場合、古い選択を実行しない。snapshotは過去3秒、時計差による未来1秒までを許容する。

非戦闘・非ポーズ・通常会話中だけ受付可能。workshop・川柳生成・Web・学習の所有権を奪わない。生成中に新しい入力が入った場合は古い結果を破棄する。すでに発行した命令は後の発言だけでは取り消さず、同じIDの再配送・5秒の期限・Fabricの再検査で管理する。

着地点はプレイヤー付近2〜5ブロックを調べ、2×2の支持面と3ブロックの空間、危険ブロック・液体・他のentity・ワールド境界・高さ・読込済み範囲を検査する。準備10 tick後にも所有・拘束・プレイヤー位置・足場を検査し、成立しなければ移動しない。

## 出現演出

準備光の後に同じ馬を移動し、前脚を上げる状態にする。クライアントで到着と立ち上がりの進行を確認した同じtickに、馬のいななきと中心が白・周囲が黄色の粒子を出す。粒子は周囲へ散り、明滅しながら消える。外部素材の追加はなく、vanillaのglitter spriteを使う。

移動ACKは移動成功の証拠であり、音や描画の観測証拠ではない。演出待ちがタイムアウトしても二度移動しない。実機での色・タイミング・馬体との重なりは別途確認が必要。

## 通信契約

専用endpointは設けず、既存の登録・game-events・commands・command_results・ACKを使う。

- 登録の実行権限: `execution_capabilities: ["integrated_server.horse.recall.v1"]`
- 観測: `owned_horses` は任意。省略はこの機能の観測が無いことを意味する。
- 一覧: `schema_version:1, status:available|unavailable, world_id, revision, observed_at, player_uuid, dimension, recent_mount_id, candidates`
- 各候補: `entity_id, custom_name, tamed, owned_by_player, alive, dimension, has_passengers, leashed`
- 命令: `type:recall_horse, command_id, issued_at, expires_at, world_id, player_uuid, dimension, entity_id`
- 結果: `command_type:recall_horse, command_id, status, executed_at, detail_code, entity_id, world_id`。識別子はトップレベルに置く。

`world_id` は接続・ClientWorldが変わると新しくする。同じ次元名の別世界へ命令を持ち込まない。revisionが増えても対象情報が一致すればよい。未知IDの結果はACKするが成功の証拠にせず、正しい命令・種類・個体・世界・期限が一致した成功だけを一度記録する。

## 配置と検証

- `dogido-rust/src/assist/horse_recall/`: 依頼・候補選択の契約、原文/個体検証、命令の待ち行列。
- `dogido-rust/src/dialogue/horse_recall_runtime.rs`: 通常会話からの呼出し、最新状態への適用、実行結果の会話への接続。
- `adapter/minecraft-fabric/.../horse_recall/`: 所有馬観測、実移動、着地点、演出。
- `dogido-rust/scripts/check_horse_recall.py`: 実HTTPと模擬LLM/TTSによる接続・候補の段階的公開・再配送・ACK・取消・改名検査。

比較結果と採用方式は [API / JSONの適性](2026-10-11-api-json-suitability.md) を参照。実モデル比較は選択結果までを検証し、Minecraftの馬は動かさない。
