use super::*;

pub(super) async fn page() -> Html<&'static str> {
    Html(include_str!("catalog.html"))
}
pub(super) async fn snapshot(State(state): State<AppState>) -> ApiReply {
    let Some(d) = state.dialogue else {
        return ApiReply::ok(
            json!({"enabled":false,"entries":crate::catalog_editor::entries(),"corrections":[]}),
        );
    };
    match d.catalog_view().await {
        Ok(v) => ApiReply::ok(v),
        Err(e) => failure(e),
    }
}
fn failure(error: anyhow::Error) -> ApiReply {
    let reason = error.to_string();
    let status = match reason.as_str() {
        "invalid_surface"
        | "invalid_reading"
        | "invalid_wrong_reading"
        | "catalog_entry_mismatch" => StatusCode::UNPROCESSABLE_ENTITY,
        "reading_changed" | "memory_disabled" => StatusCode::CONFLICT,
        "server_stopping" => StatusCode::SERVICE_UNAVAILABLE,
        _ => StatusCode::INTERNAL_SERVER_ERROR,
    };
    if status == StatusCode::INTERNAL_SERVER_ERROR {
        tracing::warn!(event="catalog_edit_failed", %error);
        ApiReply::error(status, json!("reading_storage_failed"))
    } else {
        ApiReply::error(status, json!(reason))
    }
}
pub(super) async fn save(
    State(state): State<AppState>,
    payload: Result<Json<crate::catalog_editor::Edit>, JsonRejection>,
) -> Result<ApiReply, ApiReply> {
    let edit = parse(payload)?;
    let Some(d) = state.dialogue else {
        return Ok(ApiReply::unsupported("catalog_edit"));
    };
    Ok(match d.edit_catalog_reading(edit).await {
        Ok(v) => ApiReply::ok(v),
        Err(e) => failure(e),
    })
}
pub(super) async fn remove(
    State(state): State<AppState>,
    payload: Result<Json<crate::catalog_editor::Remove>, JsonRejection>,
) -> Result<ApiReply, ApiReply> {
    let edit = parse(payload)?;
    let Some(d) = state.dialogue else {
        return Ok(ApiReply::unsupported("catalog_edit"));
    };
    Ok(match d.remove_catalog_reading(edit).await {
        Ok(v) => ApiReply::ok(v),
        Err(e) => failure(e),
    })
}
