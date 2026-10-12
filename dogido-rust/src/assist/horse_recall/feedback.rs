//! 既存の呼び戻し結果コードを、会話へ戻す観測事実と短い案内へ変える。
//! 未ロードだけでは距離・次元・消失を区別できない。原因を補作せず、成功未確認と未実行も分ける。

/// (短期履歴へ渡す確認済みの事実, プレイヤーへの案内)。保存や追加のモデル呼出しは行わない。
pub fn failure(code: &str) -> (&'static str, &'static str) {
    match code {
        "horse_not_loaded" | "horse_no_longer_loaded" => (
            "現在読み込まれている範囲で対象の個体を確認できなかった。遠距離・別次元・消失のどれが原因かは未確認。",
            "声が届かへんかったみたいや。",
        ),
        "no_water_landing" => (
            "プレイヤー付近に、横3×3以上の水域と身体の空間を備えた着地点が見つからず、移動は実行されなかった。",
            "近くに、横3×3の水域で安全に出られる場所が見つからへんかったわ。まだ呼んでへんで。",
        ),
        "no_safe_landing" => (
            "プレイヤー付近に身体が収まる安全な着地点が見つからず、移動は実行されなかった。",
            "近くに安全に出られる場所が見つからへんかったわ。まだ呼んでへんで。",
        ),
        "horse_occupied" => (
            "対象に乗員がいるか、対象が別の乗り物に乗っていたため、移動は実行されなかった。",
            "その子に誰か乗っとるか、その子が何かに乗っとるから、今は呼べへんで。",
        ),
        "horse_leashed" => (
            "対象がリードでつながれているか、他個体を牽引していたため、移動は実行されなかった。",
            "その子がリードでつながっとるか、ほかの子を引いとるから、今は呼べへんで。",
        ),
        "horse_not_owned" => (
            "対象が本人の所有、または許可された種類の名札と乗用装備という条件を満たさず、移動は実行されなかった。",
            "その子は今の呼び戻し条件に合わへんかったわ。所有者と名札、鞍かハーネスを確認してな。",
        ),
        "mount_not_adult" => (
            "対象が成体ではないため、移動は実行されなかった。",
            "その子はまだ子どもやから、呼び戻せへんで。",
        ),
        "horse_not_alive" => (
            "対象の生存を確認できず、移動は実行されなかった。",
            "その子が生きとることを確認できへんかったから、呼び戻しは見送ったで。",
        ),
        "landing_changed" => (
            "準備中にプレイヤー位置または着地点の条件が変わり、移動は実行されなかった。",
            "出す場所か立ち位置の条件が変わったから、今回は呼び戻しを見送ったで。",
        ),
        "horse_target_changed" => (
            "選択後の対象の名前・種類・所有・装備・乗車などの状態が変わり、移動命令は発行されなかった。",
            "その子の状態が変わったから、呼び戻しは見送ったで。",
        ),
        "horse_not_eligible" => (
            "対象が現在の呼び戻し条件を満たしていないため、移動命令は発行されなかった。",
            "その子は今、呼び戻せる条件に合わへんかったわ。",
        ),
        "dimension_changed" | "stale_world" | "horse_snapshot_changed" => (
            "次元または接続先の状態が変わり、この呼び戻しは実行されなかった。",
            "次元かワールドの状態が変わったから、今回は呼び戻しを見送ったで。",
        ),
        "server_result_timeout" | "expired" | "result_outside_validity_window" => (
            "期限内の移動結果を確認できなかった。実際に移動したかは未確認。",
            "呼び戻しの結果を時間内に確認できへんかったわ。その子の様子を見てな。",
        ),
        "result_mismatch" | "result_type_mismatch" => (
            "個体または命令の結果が一致せず、移動成功を確認できなかった。",
            "呼び戻しを確認できへんかったわ。呼んだ子の状態を見てな。",
        ),
        _ => (
            "呼び戻しの成功を確認できなかった。具体的な原因と実際の移動は未確認。",
            "呼び戻しを確認できへんかったわ。呼んだ子の状態を見てな。",
        ),
    }
}

/// モデルには確認した理由を出来事として渡す。発話済みとはせず、川柳材料にも使わない。
pub fn failure_story(code: &str) -> String {
    format!(
        "依頼された乗れる動物の呼び戻しは成功を確認できなかった。理由: {}",
        failure(code).0
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn absent_target_is_not_diagnosed_as_distance_and_unconfirmed_results_do_not_claim_no_movement()
    {
        assert!(
            failure("horse_not_loaded")
                .0
                .contains("どれが原因かは未確認")
        );
        assert_eq!(
            failure("horse_not_loaded").1,
            "声が届かへんかったみたいや。"
        );
        assert!(failure("no_water_landing").0.contains("横3×3"));
        assert!(!failure("horse_not_eligible").0.contains("変わり"));
        assert!(failure("horse_target_changed").0.contains("変わり"));
        for code in [
            "server_result_timeout",
            "execution_failed",
            "teleport_not_applied",
            "unknown",
        ] {
            assert!(!failure(code).1.contains("まだ呼んでへん"));
            assert!(!failure(code).0.contains("移動は実行されなかった"));
        }
    }
}
