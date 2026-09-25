use super::*;
#[derive(Clone, Default)]
pub(super) struct Places {
    biome: Option<String>,
    structure: Option<String>,
    pending_biome: Option<(String, String)>,
    pending_structure: Option<String>,
    biome_comments: HashMap<String, u64>,
    structure_comments: HashMap<String, u64>,
}
impl Places {
    pub fn update(&mut self, e: &GameEvent, now: u64, s: &Settings) {
        if e.event.name != EventName::StatusSnapshot {
            return;
        }
        let structure = e
            .world
            .structure
            .as_deref()
            .map(catalog::norm)
            .filter(|s| !s.is_empty());
        if structure != self.structure {
            self.structure = structure.clone();
            self.pending_structure = None;
            if let Some(key) = structure {
                self.pending_biome = None;
                if catalog::structure(&key).is_some()
                    && elapsed(
                        now,
                        self.structure_comments.get(&key).copied(),
                        s.ms("structure_comment_cooldown_ms"),
                    )
                {
                    self.pending_structure = Some(key);
                }
            }
        }
        let biome = e
            .world
            .biome
            .as_deref()
            .map(|s| s.trim().to_lowercase())
            .filter(|s| !s.is_empty());
        if biome == self.biome {
            return;
        }
        self.biome = biome.clone();
        self.pending_biome = None;
        if self.structure.is_some() {
            return;
        }
        let Some(biome) = biome else {
            return;
        };
        if !elapsed(
            now,
            self.biome_comments.get(&biome).copied(),
            s.ms("special_biome_comment_cooldown_ms"),
        ) {
            return;
        }
        let phase = if surroundings::phase(e) == Some("night") {
            "night"
        } else {
            "day"
        };
        let lines = catalog::biome_lines(&biome, phase);
        if lines.is_empty() {
            return;
        }
        let seed = format!("{biome}:{phase}");
        let i = seed.chars().map(|c| c as usize).sum::<usize>() % lines.len();
        self.pending_biome = Some((biome, lines[i].clone()));
    }
    pub fn dimension_changed(&mut self) {
        self.structure = None;
        self.clear_pending();
    }
    pub fn clear_pending(&mut self) {
        self.pending_biome = None;
        self.pending_structure = None;
    }
    pub fn action(
        &mut self,
        e: &GameEvent,
        now: u64,
        f: &AmbientFocus,
        s: &Settings,
    ) -> Option<Speech> {
        if f.boss_presence {
            self.clear_pending();
            return None;
        }
        if f.ominous_presence && self.biome.as_deref() != Some("deep_dark") {
            self.clear_pending();
            return None;
        }
        if let Some(key) = self.pending_structure.take() {
            let entry = catalog::structure(&key)?;
            let group = entry["group_id"].as_str()?;
            let fallbacks = &catalog::exploration()["structure"]["entry_fallbacks"];
            let template = fallbacks[group]
                .as_str()
                .or_else(|| fallbacks["default"].as_str())?;
            let text = template.replace("{label}", entry["label"].as_str().unwrap_or(&key));
            let mut details = common_details(e, s);
            details.as_object_mut()?.extend(json!({"structure":key,"structure_label":entry["label"],"structure_note":entry["note"],"group_label":entry["group_label"],"__ambient_guard":{"structure":key}}).as_object()?.clone());
            if group == "overworld_underground" {
                details["biome"] = "地下".into();
            }
            self.structure_comments.insert(key, now);
            return Some(leaf("structure_entry", text, details, 0.55));
        }
        let (biome, mut text) = self.pending_biome.take()?;
        if biome == "deep_dark"
            && f.ominous_presence
            && let Some(line) = catalog::biome_lines("deep_dark", "ominous").first()
        {
            text = line.clone();
        }
        self.biome_comments.insert(biome, now);
        let mut speech = Speech::new("special_biome_entry", text);
        speech.scope = Scope::Safe;
        Some(speech)
    }
}
