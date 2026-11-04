# 身体から見た敵の外周マージンの接続

2026-11-04。[視認と注意の分離](2026-11-04-vision-and-attention.md)で見つかった、敵側の既存外周の接続不足を修正する。

## 変更

`ThreatObserver` は通常範囲を `max(max_threat_distance, visible_threat_distance)`、外周を通常範囲+5blockとしていた。しかし身体collectorは、外周の許可に使う個体別の視認台帳を更新しておらず、実rayへも通常範囲だけを渡していた。

- 身体の走査結果も既存 `filterVisibleThreats` と `confirmedVisibleTicks` へ接続する。追跡用の別台帳は増設しない。
- 身体は現在見える敵を初回から登録する。player用の確認待ちと遮蔽猶予を身体の現在視認へ持ち込まない。
- 距離判定と実rayは同じUUIDの範囲を使う。既定は新規16block、視認済みの同じ個体だけ21blockまで。設定を24blockへ変えた場合は外周29blockとなる。
- 同種でも未確認の別UUIDは外周から新規取得しない。遮蔽・視野外・走査からの消失・外周離脱・resetで視認を解除し、外周だけでの再登場は取得しない。
- 敵対判定、本人の交戦、警告・川柳中断、音源の紐付け、FOV・遮蔽の実測、HTTP形式とプロンプトは既存処理を使う。player側の確認待ち・遮蔽猶予は維持する。

## 確認

模擬Sightへ渡る通常/外周距離、身体での即時登録、別UUID、境界往復、遮蔽・消失・reset、設定変更、player側の確認待ちを自動検査した。追加4件を含むadapter全384件と配布JARのビルドが成功。Minecraftの実世界の走査と発言は未確認。

Minecraftの停止を再確認し、通常modsのadapterだけを更新済み。他のModが変わっていないことと、build出力・導入先のSHA-256一致を確認した。次回Minecraft起動から反映する。本体の再起動は不要。

- SHA-256: `f14b58cb2dec7d007c03fcab7d44d0f18c87cb52c38c9505671c41879487e9c6`
- 導入記録・直前adapter: `.dogido_tools/checkpoints/2026-11-04-hostile-margin/`
