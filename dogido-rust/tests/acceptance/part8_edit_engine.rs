//! パート8の実編集Engineへの試験用入口。モデルの意味解釈は模擬せず、与えた解釈済み語句の受付と差分構成だけを既存処理で確認する。
use super::*;
use crate::{workshop::tools::Call, workshop_edit::Pending};
use serde_json::json;

pub(crate) fn native_pending(
    view: &Value,
    raw: &str,
    target: usize,
    args: Value,
) -> Result<Pending> {
    let call = Call::decode_for_target(
        json!({"role":"assistant","content":null,"tool_calls":[{
            "id":"acceptance-edit","type":"function","function":{"name":"stage_player_edit","arguments":args.to_string()}}
        ]}),
        view,
        raw,
        Some(target),
    )?;
    let Call::PlayerEdit { frame, .. } = call else {
        anyhow::bail!("wrong native operation")
    };
    pending(&frame)
}

pub(crate) fn candidate_pending(view: &Value) -> Result<Pending> {
    pending(&json!({"op":"player_edit","workshop":view,"text":"",
        "proposal":view["conversation_candidate"]["proposal"]}))
}

fn pending(frame: &Value) -> Result<Pending> {
    let Output::PlayerEdit(PlayerEditResult::Validated(edit)) = Engine::default().project(frame)?
    else {
        anyhow::bail!("interpreted edit was not accepted")
    };
    let base = Snapshot::from_view(&frame["workshop"])?;
    Pending::stage(
        &base.current_lines,
        &base.current_lines,
        edit.lines,
        edit.target_line_index.context("missing target")?,
    )
}
