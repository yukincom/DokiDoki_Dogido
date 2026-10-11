//! 既存tracingイベントを端末で読める段落にする。通信・保存用JSONには触れない。
use std::fmt;

use serde_json::Value;
use tracing::{Event, Subscriber, field::{Field, Visit}};
use tracing_subscriber::{
    fmt::{FmtContext, FormattedFields, format::{FormatEvent, FormatFields, Writer}},
    registry::LookupSpan,
};

pub(crate) struct ConsoleLog;

#[derive(Default)]
struct Fields(Vec<(String, String)>);

impl Visit for Fields {
    fn record_str(&mut self, field: &Field, value: &str) {
        self.0.push((field.name().into(), format!("{value:?}")));
    }

    fn record_debug(&mut self, field: &Field, value: &dyn fmt::Debug) {
        self.0.push((field.name().into(), format!("{value:?}")));
    }
}

impl<S, N> FormatEvent<S, N> for ConsoleLog
where
    S: Subscriber + for<'a> LookupSpan<'a>,
    N: for<'a> FormatFields<'a> + 'static,
{
    fn format_event(
        &self,
        ctx: &FmtContext<'_, S, N>,
        mut writer: Writer<'_>,
        event: &Event<'_>,
    ) -> fmt::Result {
        let mut fields = Fields::default();
        event.record(&mut fields);
        write!(writer, "\n{} [{}]",
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S%.3f %:z"),
            event.metadata().level())?;
        // 小さいscalarのkey=valueは維持し、既存のPID/取消ログ読取も壊さない。
        for (name, value) in &fields.0 {
            if !is_block(value) {
                write!(writer, " {name}={}", printable(value))?;
            }
        }
        writeln!(writer)?;
        if let Some(scope) = ctx.event_scope() {
            for span in scope.from_root() {
                let ext = span.extensions();
                if let Some(fields) = ext.get::<FormattedFields<N>>()
                    && !fields.is_empty()
                {
                    write_value(&mut writer, span.name(), &fields.to_string())?;
                }
            }
        }
        for (name, value) in fields.0 {
            if is_block(&value) {
                write_value(&mut writer, &name, &value)?;
            }
        }
        Ok(())
    }
}

fn is_block(raw: &str) -> bool {
    match serde_json::from_str::<Value>(raw) {
        Ok(Value::Array(_) | Value::Object(_)) => true,
        Ok(Value::String(value)) => value.contains('\n') || value.chars().count() > 100,
        _ => raw.contains('\n') || raw.chars().count() > 100,
    }
}

fn write_value(writer: &mut impl fmt::Write, name: &str, raw: &str) -> fmt::Result {
    let formatted = match serde_json::from_str::<Value>(raw) {
        Ok(value @ (Value::Array(_) | Value::Object(_))) =>
            serde_json::to_string_pretty(&value).unwrap_or_else(|_| raw.into()),
        // Debug形式の文字列も実際の改行に戻す。
        Ok(Value::String(value)) => value,
        _ => raw.into(),
    };
    if !formatted.contains('\n') && formatted.chars().count() <= 100 {
        return writeln!(writer, "  {name}: {}", printable(&formatted));
    }
    writeln!(writer, "  {name}:")?;
    for line in formatted.lines() {
        // 日本語もバイト境界で壊さず折り返す。値は省略しない。
        let clean = printable(line);
        let mut rest = clean.as_str();
        while rest.chars().count() > 100 {
            let split = rest.char_indices().nth(100).unwrap().0;
            writeln!(writer, "    {}", &rest[..split])?;
            rest = &rest[split..];
        }
        writeln!(writer, "    {rest}")?;
    }
    Ok(())
}

fn printable(text: &str) -> String {
    text.chars().flat_map(|c| {
        if c.is_control() { c.escape_default().collect::<Vec<_>>() } else { vec![c] }
    }).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io, sync::{Arc, Mutex}};

    #[derive(Clone)]
    struct Buffer(Arc<Mutex<Vec<u8>>>);
    impl io::Write for Buffer {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            self.0.lock().unwrap().extend_from_slice(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> io::Result<()> { Ok(()) }
    }

    #[test]
    fn real_events_keep_job_context_and_expand_json() {
        let bytes = Arc::new(Mutex::new(Vec::new()));
        let output = bytes.clone();
        let subscriber = tracing_subscriber::fmt().with_ansi(false)
            .event_format(ConsoleLog).with_writer(move || Buffer(output.clone())).finish();
        tracing::subscriber::with_default(subscriber, || {
            let span = tracing::info_span!("haiku", job_id = "test_job");
            let _entered = span.enter();
            tracing::info!(event = "haiku_candidate", round = 0,
                candidate = %serde_json::json!(["まつあかり", "くろいかがやき", "なつかしや"]));
        });
        let text = String::from_utf8(bytes.lock().unwrap().clone()).unwrap();
        assert!(text.contains("[INFO] event=\"haiku_candidate\" round=0\n"), "{text}");
        assert!(text.contains("job_id=\"test_job\""), "{text}");
        assert!(text.contains("  candidate:\n    [\n      \"まつあかり\","), "{text}");
    }

    #[test]
    fn long_unicode_and_invalid_json_remain_readable_and_complete() {
        let raw = format!("{}\nunfinished {{\u{1b}[2J", "あ".repeat(231));
        let mut text = String::new();
        write_value(&mut text, "response", &raw).unwrap();
        assert!(text.lines().all(|line| line.chars().count() <= 104));
        assert_eq!(text.matches('あ').count(), 231);
        assert!(text.contains("unfinished {\\u{1b}[2J"));
        assert!(!text.contains('\u{1b}'));
    }
}
