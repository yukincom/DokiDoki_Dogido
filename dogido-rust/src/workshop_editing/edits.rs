use super::*;
use crate::text_format::{self, ContainerFormat::QuotedRepr};
use crate::workshop_edit;
use parse::Replacement;
impl Engine {
    pub(super) fn discussion(
        &self,
        f: &Value,
        s: &Snapshot,
        proposal: &Value,
    ) -> Result<Option<crate::workshop_candidate::Draft>> {
        let input = text(&f["text"]);
        let r = if proposal.is_object() && truth(&proposal["replacement_text"]) {
            let replacement = text_format::value_text(&proposal["replacement_text"], QuotedRepr);
            let fragment = if truth(&proposal["target_fragment"]) {
                text_format::value_text(&proposal["target_fragment"], QuotedRepr)
            } else {
                String::new()
            };
            let index = &proposal["line_index"];
            if replacement.is_empty()
                || !input.contains(&replacement)
                || (!index.is_null() && !index.as_i64().is_some_and(|i| i >= 0))
            {
                return Ok(None);
            }
            Replacement {
                text: replacement,
                index: index.as_i64(),
                fragment: (!fragment.is_empty()).then_some(fragment),
            }
        } else {
            let pairs = parse::pattern("quoted_discussion")
                .captures_iter(input)
                .collect::<Vec<_>>();
            if pairs.len() != 1 || parse::explicit_lines(input).len() > 1 {
                return Ok(None);
            }
            Replacement {
                text: pairs[0]["alternative"].into(),
                fragment: Some(pairs[0]["target"].into()),
                index: parse::explicit_line(input).map(|i| i as i64),
            }
        };
        let result = self.revise(&f["workshop"], s, &r)?;
        Ok(Some(crate::workshop_candidate::Draft {
            proposal: crate::workshop_candidate::Proposal {
                line_index: result.target_line_index.or(r.index.map(|i| i as usize)),
                target_fragment: r.fragment.unwrap_or_default(),
                replacement_text: r.text,
            },
            evidence: input.into(),
            validation_codes: result.failure_reasons,
        }))
    }
    pub(super) fn player_edit(&self, f: &Value, s: &Snapshot) -> Result<PlayerEditResult> {
        let proposal = f["proposal"]
            .as_object()
            .context("missing player edit proposal")?;
        let input = text(&f["text"]);
        let conflict = |code: &str| PlayerEditResult::Rejected {
            text: None,
            failure_reasons: vec![code.into()],
        };
        // Invalid fields must never become absent and acquire a different target
        // from a retained line or an earlier discussion candidate.
        if proposal
            .get("line_index")
            .is_some_and(|i| !i.is_null() && !i.as_i64().is_some_and(|i| (0..3).contains(&i)))
        {
            return Ok(conflict("target_conflict"));
        }
        if proposal
            .get("target_fragment")
            .is_some_and(|v| !v.is_null() && !v.is_string())
        {
            return Ok(conflict("target_fragment_not_readable"));
        }
        let Some(replacement_text) = proposal.get("replacement_text").and_then(Value::as_str)
        else {
            return Ok(conflict("not_hiragana"));
        };
        let mut replacement = Replacement {
            text: replacement_text.into(),
            index: proposal.get("line_index").and_then(Value::as_i64),
            fragment: proposal
                .get("target_fragment")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .map(str::to_owned),
        };
        // LLMが対象を返した場合は、その行と実際の旧語をreviseで照合する。
        // 対象が欠ける場合だけ、既存の一意な原文参照・保持対象から補完する。
        if replacement.index.is_none() {
            let explicit = parse::explicit_lines(input);
            if explicit.len() == 1 {
                replacement.index = explicit.first().map(|i| *i as i64);
            }
        }
        if replacement.fragment.is_none() && replacement.index.is_none() {
            replacement.fragment = self.mentioned_fragment(s, input, &replacement.text)?;
        }
        let discussed = &f["workshop"]["conversation_candidate"]["proposal"];
        if replacement.fragment.is_none() && replacement.index.is_none() && discussed.is_object() {
            let discussed: crate::workshop_candidate::Proposal =
                serde_json::from_value(discussed.clone()).context("invalid discussion proposal")?;
            replacement.fragment =
                (!discussed.target_fragment.is_empty()).then_some(discussed.target_fragment);
            replacement.index = discussed
                .line_index
                .map(i64::try_from)
                .transpose()
                .context("invalid discussion line index")?;
        }
        if replacement.fragment.is_none()
            && replacement.index.is_none()
            && let Some(target) =
                crate::workshop_target::Target::from_view(&f["workshop"], editing_records(s))
        {
            replacement.index = Some(target.line_index as i64);
            replacement.fragment = (!target.fragment.is_empty()).then_some(target.fragment);
        }
        // 確定した旧語・置換語を原文から取り直さず、対象行の実際の語へ適用する。
        self.revise(&f["workshop"], s, &replacement)
            .map(PlayerEditResult::Validated)
    }

    /// LLMの置換語を現在の一行へ適用し、表記と取得できた読みを返す。
    /// 読み・音数・語彙・他行との表現重複で拒否しない。未確定の読みは空のまま保持する。
    /// 対象と元句の対応、対象外の行不変はこの差分構築と保存前のCASで守る。
    fn revise(&self, view: &Value, s: &Snapshot, r: &Replacement) -> Result<EditResult> {
        let base = workshop_edit::editing_text(&s.current_lines);
        let failure = |code: &str, target: Option<usize>| EditResult {
            text: None,
            base_text: base.clone(),
            surface_text: None,
            lines: vec![],
            edits: vec![],
            failure_reasons: vec![code.into()],
            target_line_index: target,
        };
        if s.pending_revision.as_ref().is_some_and(|s| !s.is_empty())
            && truth(&view["pending"]["generated_basis"])
        {
            return Ok(failure("pending_source_conflict", None));
        }
        let mut source = editing_records(s).to_vec();
        if s.current_lines.len() != 3 || source.len() != 3 {
            return Ok(failure("invalid_line_records", None));
        }
        let fragment = r.fragment.as_deref().filter(|v| !v.is_empty());
        let normalized_fragment = match fragment {
            Some(value)
                if !source.iter().any(|line| {
                    line.surface_text.contains(value) || line.reading_text.contains(value)
                }) =>
            {
                self.normalized(value, true)?
            }
            _ => None,
        };
        let match_in = |line: &HaikuLine| -> Option<String> {
            let fragment = fragment?;
            if line.surface_text.contains(fragment) {
                Some(fragment.to_owned())
            } else if !line.reading_text.is_empty() && line.reading_text.contains(fragment) {
                Some(fragment.to_owned())
            } else {
                normalized_fragment
                    .as_ref()
                    .filter(|v| {
                        !line.reading_text.is_empty() && line.reading_text.contains(v.as_str())
                    })
                    .cloned()
            }
        };
        let matches: Vec<usize> = source
            .iter()
            .enumerate()
            .filter_map(|(i, line)| match_in(line).map(|_| i))
            .collect();
        let target = match r.index {
            Some(index) if (0..3).contains(&index) => {
                let index = index as usize;
                if fragment.is_some() && !matches.contains(&index) {
                    return Ok(failure("target_conflict", Some(index)));
                }
                index
            }
            Some(_) => return Ok(failure("target_conflict", None)),
            None if matches.len() == 1 => matches[0],
            None => {
                return Ok(failure(
                    if matches.len() > 1 {
                        "ambiguous_target_fragment"
                    } else {
                        "missing_target"
                    },
                    None,
                ));
            }
        };
        // 表記は本人の指定語を保つ。対になった引用符と外側の空白だけを除き、
        // 句読点の読み向け正規化は後段の normalized に任せる。
        let replacement = parse::unquote(&r.text);
        if replacement.is_empty() || replacement.contains(['\n', '\r']) {
            return Ok(failure("invalid_surface", Some(target)));
        }
        let old = &source[target];
        let surface = if let Some(found) = match_in(old) {
            let text = if old.surface_text.contains(&found) {
                &old.surface_text
            } else {
                &old.reading_text
            };
            if text.matches(&found).count() != 1 {
                return Ok(failure("ambiguous_target_fragment", Some(target)));
            }
            text.replacen(&found, replacement, 1)
        } else {
            replacement.to_owned()
        };
        if surface == old.surface_text {
            return Ok(failure("no_change", Some(target)));
        }
        // 確定済みの旧行・旧語・置換語の読みを合成できるときだけ保持する。
        // 完成行をもう一度辞書へ送り直さず、未確定なら原文表記と空の読みを返す。
        let replacement_reading = self.normalized(replacement, true)?;
        let reading = if let Some(found) = match_in(old) {
            if found == old.surface_text || found == old.reading_text {
                replacement_reading.unwrap_or_default()
            } else if old.reading_text.is_empty() {
                String::new()
            } else {
                let old_reading = if old.reading_text.contains(&found) {
                    Some(found)
                } else {
                    self.normalized(fragment.unwrap(), true)?
                };
                match (old_reading, replacement_reading) {
                    (Some(from), Some(to)) if old.reading_text.matches(&from).count() == 1 => {
                        old.reading_text.replacen(&from, &to, 1)
                    }
                    _ => String::new(),
                }
            }
        } else {
            replacement_reading.unwrap_or_default()
        };
        let line = &mut source[target];
        line.surface_text = surface;
        line.reading_text = reading;
        line.provenance = "player_explicit".into();
        line.source_atom_ids.clear();
        line.source_atoms.clear();
        let edits = s
            .current_lines
            .iter()
            .zip(&source)
            .enumerate()
            .filter(|(_, (old, new))| {
                old.surface_text != new.surface_text || old.reading_text != new.reading_text
            })
            .map(|(i, (old, new))| workshop_edit::LineEdit {
                line_index: i,
                expected_text: workshop_edit::editing_text(std::slice::from_ref(old)),
                replacement_text: workshop_edit::editing_text(std::slice::from_ref(new)),
                provenance: new.provenance.clone(),
                atom_ids: None,
            })
            .collect::<Vec<_>>();
        if edits.is_empty() {
            return Ok(failure("no_change", Some(target)));
        }
        Ok(EditResult {
            text: Some(workshop_edit::editing_text(&source)),
            base_text: base,
            surface_text: Some(workshop_edit::surface(&source)),
            lines: source,
            edits,
            failure_reasons: vec![],
            target_line_index: Some(target),
        })
    }
}
