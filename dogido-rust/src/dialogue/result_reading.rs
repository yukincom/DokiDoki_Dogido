//! Final-text reading over the existing reverse dialogue-helper protocol.
//! The caller validates raw text and all result metadata before entering here.
use crate::tts_reading::{self, Step, tokens};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, AsyncWrite, AsyncWriteExt, Lines};

const FRAME_LIMIT: usize = 1_000_000;

pub(super) async fn finish<W, R>(
    frame: &mut Value,
    engine: &str,
    stdin: &mut W,
    stdout: &mut Lines<R>,
) -> Result<()>
where
    W: AsyncWrite + Unpin,
    R: AsyncBufRead + Unpin,
{
    ensure!(
        frame.get("spoken_text").is_none(),
        "helper result must contain raw text, not spoken_text"
    );
    let Some(text) = frame.get("text") else {
        // Routing, recall handoff, unsupported and other payload-only results.
        return Ok(());
    };
    let text = text
        .as_str()
        .context("helper result text must be a string")?;
    let spoken = match tts_reading::prepare(text, Some(engine), None) {
        Step::Ready(spoken) => spoken,
        Step::NeedsUnidic { source } => {
            let request_id = uuid::Uuid::new_v4().to_string();
            let request = serde_json::to_vec(&json!({
                "op":"tts_tokens", "schema_version":1, "request_id":request_id, "text":source
            }))?;
            ensure!(
                request.len() < FRAME_LIMIT,
                "helper token request too large"
            );
            stdin.write_all(&request).await?;
            stdin.write_all(b"\n").await?;
            stdin.flush().await?;
            // The previous result line is fully consumed and this is the final
            // exchange, so read the underlying buffer with a hard byte bound.
            let mut bytes = Vec::new();
            stdout
                .get_mut()
                .take((FRAME_LIMIT + 1) as u64)
                .read_until(b'\n', &mut bytes)
                .await?;
            ensure!(bytes.len() <= FRAME_LIMIT, "helper token frame too large");
            ensure!(
                bytes.last() == Some(&b'\n'),
                "dialogue helper ended before token result"
            );
            let response = serde_json::from_slice(&bytes).context("invalid helper token JSON")?;
            let dictionary = tokens::decode(response, &request_id)?;
            tts_reading::finish(&source, dictionary.as_deref())
        }
    };
    // Serialize a projection before mutation: a failure cannot leave a usable reply.
    ensure!(
        serde_json::to_vec(&json!({"spoken_text":spoken}))?.len() < FRAME_LIMIT,
        "helper reading result too large"
    );
    frame["spoken_text"] = spoken.into();
    Ok(())
}
