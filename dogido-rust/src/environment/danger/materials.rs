//! コードで選んだ危険・退避の機会を、既存の環境生成へ渡す観測材料にする。
//! 返答本文は作らず、主体と実測、一般的性質、生成中に変えてはいけない分岐を分離する。
use crate::events::GameEvent;
use serde_json::{Value, json};

pub(crate) fn supports(kind: &str) -> bool {
    matches!(
        kind,
        "player_hazard"
            | "magma_block"
            | "damaging_light"
            | "darkness_advice"
            | "submerged_darkness"
            | "night_warning_surface"
            | "night_warning_cave"
            | "emergency_shelter_advice"
            | "emergency_shelter_morning"
            | "end_portal_frame"
    )
}

pub(crate) fn guard(kind: &str, event: &GameEvent) -> Value {
    match kind {
        "emergency_shelter_advice" => json!({"bed_carried":super::geometry::bed(event),
            "nearby_bed":event.world.nearby_bed_count.unwrap_or(0)>0}),
        "night_warning_cave" => json!({"time_phase":super::geometry::phase(event)}),
        _ => Value::Null,
    }
}

/// 時間帯・ベッド条件は判断時点で保持する。生成前後に本文を書き換えても照合根拠を失わない。
pub(crate) fn retain_branch(
    mut action: crate::combat::model::Speech,
    event: &GameEvent,
) -> crate::combat::model::Speech {
    let branch = guard(action.kind, event);
    if !branch.is_null() {
        action.leaf = Some(crate::combat::model::LeafRequest {
            kind: crate::environment::reaction::KIND.into(),
            details: json!({"__reaction_guard":{"danger_branch":branch}}),
            temperature: 0.65,
        });
    }
    action
}

pub(crate) fn observations(kind: &str, hazard: &Value, event: &GameEvent) -> Value {
    let player = crate::conversation_observation::subjects::player_state(event)
        .unwrap_or_else(|_| json!({"subject":"プレイヤー","status":"unavailable"}));
    let world = &event.world;
    json!({"event_kind":kind,"hazard":hazard,
        "subject":if matches!(kind,"player_hazard"|"magma_block") {"player"} else {"dogido"},
        "subject_states":{"player":player},
        "player_standing_on_magma_block":event.player_safety_world().standing_on_magma_block,
        "dogido_surroundings":{"local_light":world.local_light,"darkness_score":world.danger_darkness_score,
        "time_phase":world.time_phase,"sky_visible":world.sky_visible,"overhead_cover":world.overhead_cover_type,
        "nearby_damaging_light_source_count":world.nearby_damaging_light_source_count,
        "nearest_damaging_light_source_distance":world.nearest_damaging_light_source_distance,
        "submerged_depth_blocks":world.submerged_depth_blocks,
        "light_materials_nearby":super::geometry::materials(event),
        "ceiling_height":world.ceiling_height,"enclosure_score":world.enclosure_score,
        "nearby_end_portal_frame_distance":world.nearby_end_portal_frame_distance},
        "player_equipment":{"light_carried":super::geometry::torch(event),
            "torch_craftable":super::geometry::craftable(event),
            "weapon_carried":super::geometry::weapon(event),"held_item":event.player.held_item},
        "shelter_branch":guard(kind,event)})
}

pub(crate) fn properties(kind: &str) -> Value {
    let note = match kind {
        "player_hazard" => {
            "溶岩への接触、炎上、落下中、空気の残量はプレイヤー本人の実測。被ダメージの原因、着地先、着地後の被害は未確認。"
        }
        "magma_block" => "足元のマグマブロックと、流体の溶岩への接触は別の状態。",
        "damaging_light" => {
            "有害な熱源の近接観測。火・点火中の焚き火・マグマブロック・溶岩などの合計で、種類や本人の接触はこの数値からは確定しない。"
        }
        "submerged_darkness" => "深く暗い水域の観測。プレイヤーの酸素不足とは別の判断。",
        "night_warning_surface" | "night_warning_cave" => {
            "ゲーム内の時間帯と現在地の観測。暗いという理由だけで夜と判断していない。"
        }
        "emergency_shelter_advice" | "emergency_shelter_morning" => {
            "現在の囲まれ方と時間帯に基づく退避の機会。本人が建てた場所とは限らない。"
        }
        "end_portal_frame" => "近くにエンドポータルの枠を観測。開通状態や本人の移動意思は未確認。",
        _ => "現在の場所の局所明るさと遮蔽に基づく機会。敵の存在や将来の被害は未確認。",
    };
    json!({"note":note})
}
