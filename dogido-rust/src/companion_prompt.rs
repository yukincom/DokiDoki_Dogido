//! One personality source for every model path that speaks as Dogido.
//! Internal extractors/validators have their own contracts and do not impersonate him.
//! JSONはラボ・配布と共有する編集用資産。配置先がPythonパッケージでも、発話要求の構築はRust。
//! 現在はLazyLockの初回参照でJSONを読む。ビルド時・起動時の全キー検査は行っていない。
use serde_json::Value;
use std::sync::LazyLock;

static TEXT: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../dogido_server/llm/companion_prompts.json"
    ))
    .expect("companion prompt source")
});

pub fn base() -> &'static str {
    TEXT["base"].as_str().unwrap()
}
pub fn mode(mode: &str) -> &'static str {
    TEXT["modes"][mode].as_str().unwrap_or("")
}
pub fn image_move_notice() -> &'static str {
    TEXT["image_move_notice"].as_str().unwrap()
}
pub fn image_body_contract() -> &'static str {
    TEXT["image_body_contract"].as_str().unwrap()
}
pub fn look_player_contract() -> &'static str {
    TEXT["look_player_contract"].as_str().unwrap()
}
pub fn chat_image() -> &'static str {
    TEXT["chat_image"].as_str().unwrap()
}
pub fn grounding() -> &'static str {
    TEXT["grounding"].as_str().unwrap()
}
/// 身体観測がある通常会話へ読み方を添える。呼び名は既存会話材料のplayer_nameを使い、欠損時は役割名に戻す。
pub fn body_observation(player_name: &str) -> String {
    let name = player_name.trim();
    let player = if name.is_empty() || name == "プレイヤー" {
        "プレイヤー".to_owned()
    } else {
        format!("{name}（プレイヤー）")
    };
    TEXT["body_observation"]
        .as_str()
        .unwrap()
        .replace("{player}", &player)
}

/// 出力契約を機能専用のものへ差し替える際も、共有人格・観測方針は同じ正本を使う。
pub fn choice_contract() -> &'static str {
    TEXT["choice_contract"].as_str().unwrap()
}
pub fn speech(mode_name: &str) -> String {
    let role = if mode_name == "workshop" {
        TEXT["workshop_identity"].as_str().unwrap()
    } else {
        ""
    };
    [
        base(),
        role,
        mode(mode_name),
        TEXT["speech_contract"].as_str().unwrap(),
    ]
    .into_iter()
    .filter(|s| !s.is_empty())
    .collect::<Vec<_>>()
    .join("\n")
}
/// The caller owns the purpose; no model call classifies this branch.
#[derive(Clone, Copy)]
pub enum DialoguePurpose {
    Reply,
    Ambient,
}

/// プレイヤーの新しい入力がない環境判断の用件。
pub fn ambient_request() -> &'static str {
    TEXT["dialogue"]["ambient_request"].as_str().unwrap()
}

/// Smell guidance is attached only for a current, code-resolved smell observation.
pub fn dialogue(mode_name: &str, purpose: DialoguePurpose, smell_observed: bool) -> String {
    dialogue_with_body(mode_name, purpose, smell_observed, None)
}

/// 固定の身体説明も可変のmode・匂いより前へ置く。説明の有無と呼び名は呼出元の観測を使う。
pub fn dialogue_with_body(
    mode_name: &str,
    purpose: DialoguePurpose,
    smell_observed: bool,
    body_player: Option<&str>,
) -> String {
    compose_dialogue(mode_name, purpose, smell_observed, choice_contract(), body_player)
}

fn compose_dialogue(
    mode_name: &str,
    purpose: DialoguePurpose,
    smell_observed: bool,
    contract: &str,
    body_player: Option<&str>,
) -> String {
    let purpose_key = match purpose {
        DialoguePurpose::Reply => "reply",
        DialoguePurpose::Ambient => "ambient",
    };
    let body = body_player.map(body_observation).unwrap_or_default();
    // 文面・適用条件・system/userの役割を変えず、再利用できる接頭部を長くする。
    // 完成したpromptは保存しない。現在観測・履歴は呼出元で毎回組み立てる。
    [
        base(),
        grounding(),
        contract,
        TEXT["dialogue"][purpose_key].as_str().unwrap(),
        if matches!(purpose, DialoguePurpose::Ambient) { ambient_request() } else { "" },
        &body,
        mode(mode_name),
        if smell_observed {
            TEXT["observed_smell"].as_str().unwrap()
        } else {
            ""
        },
    ]
    .into_iter()
    .filter(|s| !s.is_empty())
    .collect::<Vec<_>>()
    .join("\n")
}

/// 身体候補がある環境判断だけ、共有の発話契約を発話＋行動の閉じた契約へ差し替える。
pub fn environment(smell_observed: bool, body_actions: bool) -> String {
    environment_with_body(smell_observed, body_actions, false)
}

pub fn environment_with_body(smell_observed: bool, body_actions: bool, body_observed: bool) -> String {
    // 契約の位置に依存した末尾置換をせず、従来と同じ条件で一つだけ選んで組む。
    let contract = if body_actions {
        TEXT["environment_body_contract"].as_str().unwrap()
    } else {
        choice_contract()
    };
    compose_dialogue(
        "normal",
        DialoguePurpose::Ambient,
        smell_observed,
        contract,
        body_observed.then_some("プレイヤー"),
    )
}

/// 当回に許可された行動だけの完成例。inspectの一時IDも当回の候補から埋める。
/// 例の選択は表示だけで、行動の受付・実行契約は変更しない。
pub fn environment_body_examples(options: &Value) -> String {
    let Some(actions) = options["available_actions"].as_object() else {
        return String::new();
    };
    actions.keys().filter_map(|action| {
        let mut example = TEXT["environment_body_examples"].get(action)?.clone();
        if action == "inspect" {
            let candidate = options["candidates"].as_array()?.first()?;
            example["body_action"]["target_id"] = candidate.get("id")?.clone();
        }
        Some(example.to_string())
    }).collect::<Vec<_>>().join("\n")
}

/// Expand references in generated task assets from the same source as live dialogue.
pub fn expand(text: &str) -> String {
    text.replace("__DOGIDO_SHARED_BASE__", base())
        .replace(
            "__DOGIDO_WORKSHOP_IDENTITY__",
            TEXT["workshop_identity"].as_str().unwrap(),
        )
        .replace("__DOGIDO_WORKSHOP_MODE__", mode("workshop"))
}

#[cfg(test)]
mod tests;
