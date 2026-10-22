//! 実際に届いたsound eventを、会話用の音種別へ写す読み取り専用の整形。
//! 発音者や実ブロックは音IDだけでは確定しない。保持時刻と状態変更は呼出元が所有する。

/// 確定済みの発音主体だけを音種と分けて明示する。モデルの返答へ判定・語句検査は追加しない。
pub(super) fn with_emitter(description: String,emitter: Option<&str>) -> String {
    match emitter {
        Some(emitter) => format!("音：{}／音の由来：{emitter}",description.trim_end_matches("（発音者未確定）")),
        None => description,
    }
}
fn event_path(event: &str) -> &str {
    event.strip_prefix("minecraft:").unwrap_or(event)
}

/// 同じ音源の別種の音を区別する。同じIDの再配送は従来の保持時刻の照合へ戻す。
pub(super) fn event_key(key: String, event: Option<&str>) -> String {
    match event.filter(|event| !event.is_empty()) {
        Some(event) => format!("{key}:sound:{}", event_path(event)),
        None => key,
    }
}

/// カタログ名と実音IDから説明する。namedは個体照合済みの名前を使う場合だけtrue。
/// blockのstep/break/place等は共有sound groupなので、材質系と音種別までに留める。
pub(crate) fn description(
    label: Option<&str>,
    kind: &str,
    event: Option<&str>,
    named: bool,
) -> String {
    let id = event.map(event_path).unwrap_or("");
    let action = id.rsplit('.').next().unwrap_or("");
    if id.starts_with("entity.player.") {
        let action = if id.contains(".attack.") {"攻撃音"} else if id.contains(".hurt") {"被ダメージの声"}
            else if id.contains(".swim") {"泳ぐ音"} else if id.contains(".splash") {"水しぶきの音"}
            else if id.ends_with(".burp") {"げっぷの音"} else if id.ends_with(".breath") {"呼吸音"}
            else {"プレイヤー用の効果音"};
        return action.into();
    }
    if id.starts_with("block.") {
        let action_label = match action {
            "step" => Some("足音"),
            "hit" => Some("打撃音"),
            "break" => Some("破壊音"),
            "place" => Some("設置音"),
            "fall" => Some("着地音"),
            _ => None,
        };
        if let Some(action_label) = action_label {
            if named {
                return label.map_or_else(
                    || format!("{action_label}（発音者未確定）"),
                    |label| format!("{label}の{action_label}"),
                );
            }
            let material = label.map_or_else(
                || "ブロック".into(),
                |label| {
                    if matches!(action, "step" | "fall") {
                        format!("{label}系")
                    } else {
                        format!("{label}系ブロック")
                    }
                },
            );
            return format!("{material}の{action_label}（発音者未確定）");
        }
        if action == "crackle" {
            return label.map_or_else(
                || "ぱちぱちとはぜる音".into(),
                |label| format!("{label}のはぜる音"),
            );
        }
    }
    if id.starts_with("entity.") && matches!(action, "step" | "step_lava" | "step_wood") {
        return label.map_or_else(
            || "足音（発音者未確定）".into(),
            |label| format!("{label}の足音"),
        );
    }
    let vocal = id.starts_with("entity.")
        && matches!(
            action,
            "ambient" | "growl" | "pant" | "whine" | "hurt" | "death" | "purr" | "purreow" | "hiss"
        );
    match label {
        Some(label) if named && vocal => format!("{label}の声"),
        Some(label) if !named && kind == "ambient" && (vocal || event.is_none()) => {
            format!("{label}っぽい声")
        }
        Some(label) => format!("{label}の音"),
        None => "音（種別未確定）".into(),
    }
}
