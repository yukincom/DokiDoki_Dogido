use super::*;
use crate::{haiku::meter, workshop_edit};
use parse::Replacement;
impl Engine {
    pub(super) fn fixed_fragment(&self, f: &Value, s: &Snapshot) -> Result<Value> {
        let input = text(&f["text"]);
        if f["phase"] != "decide"
            || !list(&f["allowed_actions"])
                .iter()
                .any(|a| a == "stage_player_edit")
            || !parse::explicit_edit(input)
            || parse::explicit_lines(input).len() > 1
        {
            return Ok(Value::Null);
        }
        let pairs = parse::pattern("quoted_edit")
            .captures_iter(input)
            .collect::<Vec<_>>();
        let replacement = if !pairs.is_empty() {
            if pairs.len() != 1 {
                return Ok(Value::Null);
            }
            Replacement {
                text: pairs[0]["alternative"].into(),
                index: parse::explicit_line(input).map(|i| i as i64),
                fragment: Some(pairs[0]["target"].into()),
            }
        } else {
            let (status, r) = parse::replacement(input);
            if status != "accepted" {
                return Ok(Value::Null);
            }
            let mut r = r.unwrap();
            r.fragment = self.mentioned_fragment(s, input, &r.text)?;
            r
        };
        if replacement.text.is_empty()
            || !input.contains(&replacement.text)
            || replacement.fragment.is_none()
        {
            return Ok(Value::Null);
        }
        let result = self.revise(&f["workshop"], s, &replacement)?;
        if !result["text"].is_string() {
            return Ok(Value::Null);
        }
        Ok(
            json!({"action":"stage_player_edit","purpose":"improve_wording","confidence":1.0,"evidence":input,"speech":"","checks":[],
   "line_reference":{"found":false,"concept_id":"unknown","evidence":"","confidence":0.0},
   "line_proposal":{"found":true,"target_fragment":replacement.fragment,"replacement_text":replacement.text,"evidence":input,"confidence":1.0}}),
        )
    }
    pub(super) fn discussion(&self, f: &Value, s: &Snapshot, proposal: &Value) -> Result<Value> {
        let input = text(&f["text"]);
        if parse::pattern("discussion_report").is_match(input) {
            return Ok(Value::Null);
        }
        let r = if proposal.is_object() && truth(&proposal["replacement_text"]) {
            let replacement = crate::chat_catalog::text(&proposal["replacement_text"]);
            let fragment = if truth(&proposal["target_fragment"]) {
                crate::chat_catalog::text(&proposal["target_fragment"])
            } else {
                String::new()
            };
            let index = &proposal["line_index"];
            if replacement.is_empty()
                || !input.contains(&replacement)
                || (!index.is_null() && index.as_i64().is_none())
            {
                return Ok(Value::Null);
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
                return Ok(Value::Null);
            }
            Replacement {
                text: pairs[0]["alternative"].into(),
                fragment: Some(pairs[0]["target"].into()),
                index: parse::explicit_line(input).map(|i| i as i64),
            }
        };
        let result = self.revise(&f["workshop"], s, &r)?;
        Ok(
            json!({"proposal":{"line_index":result["target_line_index"].as_i64().or(r.index),"target_fragment":r.fragment.unwrap_or_default(),"replacement_text":r.text},"evidence":input,"validation_codes":result["failure_reasons"]}),
        )
    }
    pub(super) fn player_edit(&self, f: &Value, s: &Snapshot) -> Result<Value> {
        let mut p = f["proposal"]
            .as_object()
            .context("missing player edit proposal")?
            .clone();
        let input = text(&f["text"]);
        let explicit = parse::explicit_lines(input);
        if explicit.len() > 1 {
            return Ok(json!({"text":null,"failure_reasons":["target_conflict"]}));
        }
        if let Some(index) = explicit.first() {
            if !p
                .get("line_index")
                .is_none_or(|i| i.is_null() || i.as_u64() == Some(*index as u64))
            {
                return Ok(json!({"text":null,"failure_reasons":["target_conflict"]}));
            }
            p.insert("line_index".into(), json!(index));
        }
        // The player-selected target survives the short history window. A model
        // cannot silently redirect an edit; only a new player reference moves it.
        if crate::workshop_target::Target::explicit(input, editing_records(s)).is_none()
            && let Some(target) =
                crate::workshop_target::Target::from_view(&f["workshop"], editing_records(s))
        {
            if p.get("line_index")
                .and_then(Value::as_u64)
                .is_some_and(|i| i != target.line_index as u64)
            {
                return Ok(json!({"text":null,"failure_reasons":["retained_target_conflict"]}));
            }
            p.insert("line_index".into(), json!(target.line_index));
            if !p.get("target_fragment").is_some_and(truth) {
                p.insert("target_fragment".into(), json!(target.fragment));
            }
        }
        let fragment = self.mentioned_fragment(s, input, text(&p["replacement_text"]))?;
        if !p.get("target_fragment").is_some_and(truth) && fragment.is_some() {
            p.insert("target_fragment".into(), json!(fragment));
        }
        let discussed = &f["workshop"]["conversation_candidate"]["proposal"];
        if !p.get("target_fragment").is_some_and(truth)
            && p.get("line_index").is_none_or(Value::is_null)
            && discussed.is_object()
        {
            p.insert(
                "target_fragment".into(),
                discussed["target_fragment"].clone(),
            );
            p.insert("line_index".into(), discussed["line_index"].clone());
        }
        if !p.get("target_fragment").is_some_and(truth)
            && p.get("line_index").is_none_or(Value::is_null)
            && let Some(target) =
                crate::workshop_target::Target::from_view(&f["workshop"], editing_records(s))
        {
            p.insert("line_index".into(), json!(target.line_index));
            p.insert("target_fragment".into(), json!(target.fragment));
        }
        self.revise(
            &f["workshop"],
            s,
            &Replacement {
                text: text(&p["replacement_text"]).into(),
                index: p.get("line_index").and_then(Value::as_i64),
                fragment: p
                    .get("target_fragment")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .map(str::to_owned),
            },
        )
    }
    fn revise(&self, view: &Value, s: &Snapshot, r: &Replacement) -> Result<Value> {
        let base = s.surface_text.trim_matches(space).to_owned();
        let fail = |codes: Vec<String>, target: Option<usize>| json!({"text":null,"base_text":base,"surface_text":null,"lines":[],"edits":[],"failure_reasons":codes,"target_line_index":target});
        let failure = |code: &str, target: Option<usize>| fail(vec![code.into()], target);
        if s.pending_revision.as_ref().is_some_and(|s| !s.is_empty())
            && truth(&view["pending"]["generated_basis"])
        {
            return Ok(failure("pending_source_conflict", None));
        }
        let base_lines = parse::verse_lines(&base);
        let mut draft = editing_lines(s);
        if base_lines.len() != 3 || draft.len() != 3 {
            return Ok(failure("invalid_verse", None));
        }
        for line in base_lines.iter().chain(draft.iter()) {
            if self.normalized(line, true)?.as_deref() != Some(line) {
                return Ok(failure("verse_not_hiragana", None));
            }
        }
        let mut fragment_target = None;
        let mut normalized_fragment = None;
        if let Some(fragment) = r.fragment.as_ref().filter(|s| !s.is_empty()) {
            let Some(normalized) = self.normalized(fragment, true)? else {
                return Ok(failure("target_fragment_not_readable", None));
            };
            let matches = draft
                .iter()
                .enumerate()
                .filter_map(|(i, s)| s.contains(&normalized).then_some(i))
                .collect::<Vec<_>>();
            fragment_target = if let Some(i) = r
                .index
                .filter(|i| *i >= 0)
                .map(|i| i as usize)
                .filter(|i| matches.contains(i))
            {
                Some(i)
            } else if matches.len() == 1 {
                Some(matches[0])
            } else {
                return Ok(failure(
                    if matches.len() > 1 {
                        "ambiguous_target_fragment"
                    } else {
                        "target_fragment_not_found"
                    },
                    None,
                ));
            };
            let line = &draft[fragment_target.unwrap()];
            let at = line.find(&normalized).unwrap();
            let next = at + line[at..].chars().next().unwrap().len_utf8();
            if line[next..].contains(&normalized) {
                return Ok(failure("ambiguous_target_fragment", None));
            }
            normalized_fragment = Some(normalized);
        }
        if let (Some(a), Some(b)) = (r.index, fragment_target)
            && a != b as i64
        {
            return Ok(failure("target_conflict", None));
        }
        let target = r.index.or(fragment_target.map(|i| i as i64));
        let Some(target) = target.filter(|i| (0..3).contains(i)).map(|i| i as usize) else {
            return Ok(failure("missing_target", None));
        };
        let Some(normalized) = self.normalized(&r.text, true)? else {
            return Ok(failure("not_hiragana", Some(target)));
        };
        let partial = normalized_fragment
            .as_ref()
            .filter(|f| **f != draft[target]);
        let revised = partial.map_or_else(
            || normalized.clone(),
            |f| draft[target].replacen(f, &normalized, 1),
        );
        let mut reasons =
            meter::line_failure_reasons(&revised, target, s.materials.as_object().unwrap());
        if meter::count_japanese_sounds(&revised) != meter::TARGETS[target] {
            reasons.push("meter_not_exact".into())
        }
        if draft
            .iter()
            .enumerate()
            .any(|(i, l)| i != target && compact(l) == compact(&revised))
        {
            reasons.push("duplicate_line".into())
        }
        let mut dedup = Vec::new();
        for r in reasons {
            if !dedup.contains(&r) {
                dedup.push(r)
            }
        }
        if !dedup.is_empty() {
            return Ok(fail(dedup, Some(target)));
        }
        let mut source = if s.pending_revision.as_ref().is_some_and(|s| !s.is_empty()) {
            s.pending_revision_lines.clone()
        } else {
            s.current_lines.clone()
        };
        if source.len() != 3 {
            let surface = s
                .pending_revision_surface_text
                .clone()
                .unwrap_or_else(|| workshop_edit::surface(&s.current_lines));
            source = self.whole_verse(&surface, "generated")?
        }
        if source.len() != 3 {
            return Ok(failure("invalid_line_records", Some(target)));
        }
        let mut surface = r
            .text
            .trim_matches(space)
            .trim_matches(|c| "「」『』\"' 。．.!！?？…".contains(c))
            .to_owned();
        if let Some(fragment) = partial {
            let old = &source[target].surface_text;
            let spoken = r
                .fragment
                .as_deref()
                .unwrap_or("")
                .trim_matches(space)
                .trim_matches(|c| "「」『』\"'".contains(c));
            surface = if let Some(found) = [spoken, fragment.as_str()]
                .into_iter()
                .find(|s| old.matches(s).count() == 1)
            {
                old.replacen(found, &surface, 1)
            } else {
                revised.clone()
            };
            if self.normalized(&surface, true)?.as_deref() != Some(&revised) {
                surface = revised.clone()
            }
        }
        let line = &mut source[target];
        line.surface_text = surface;
        line.reading_text = revised;
        line.provenance = "player_explicit".into();
        line.source_atom_ids.clear();
        line.source_atoms.clear();
        let revised_text = workshop_edit::reading(&source);
        if revised_text == editing_lines(s).join("\n") {
            return Ok(failure("no_change", Some(target)));
        }
        draft = parse::verse_lines(&revised_text);
        let edits=(0..3).filter(|i|base_lines[*i]!=draft[*i]).map(|i|json!({"line_index":i,"expected_text":base_lines[i],"replacement_text":draft[i],"provenance":"player_explicit"})).collect::<Vec<_>>();
        // These edits are constructed from the same three base/draft lines. Runtime
        // Pending::stage rechecks identity/frozen lines/CAS before any state mutation.
        if edits.is_empty() {
            return Ok(failure("invalid_edit", Some(target)));
        }
        Ok(
            json!({"text":revised_text,"base_text":base,"surface_text":workshop_edit::surface(&source),"lines":source,"edits":edits,"failure_reasons":[],"target_line_index":target}),
        )
    }
}
