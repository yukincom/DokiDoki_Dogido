use super::*;

impl Specials {
    /// 雨→水中生存→燃焼の順でcoreが呼ぶ。
    pub fn daylight_rain(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        if !daylight(e)
            || !matches!(e.world.weather, Some(Weather::Rain | Weather::Thunder))
            || !elapsed(
                now,
                self.last_rain_at,
                s.ms("daylight_water_comment_cooldown_ms"),
            )
            || !e
                .visual_threats
                .iter()
                .any(|t| burns_in_daylight(&t.r#type) && !t.in_water && !t.on_fire)
        {
            return None;
        }
        self.last_rain_at = Some(now);
        let dry = biome(e).is_some_and(|(group, _)| group == "dry");
        let key = if !dry {
            "rain_started"
        } else if e.world.weather == Some(Weather::Thunder) {
            "dry_thunder"
        } else {
            "dry_overcast"
        };
        let mut speech = Speech::visual(
            "daylight_rain",
            catalog::text("combat", &["daylight", key]),
            e.visual_threats.iter().map(identity).collect(),
        );
        if !dry {
            speech.protect_ms = 3500;
        }
        Some(speech)
    }

    pub fn daylight_water(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        if !elapsed(
            now,
            self.last_water_at,
            s.ms("daylight_water_comment_cooldown_ms"),
        ) {
            return None;
        }
        let survivors: Vec<_> = e
            .visual_threats
            .iter()
            .filter(|t| Self::water_survivor(e, t))
            .collect();
        if survivors.is_empty() {
            return None;
        }
        self.last_water_at = Some(now);
        for t in &survivors {
            self.water_handled.insert(identity(t));
        }
        let mut text = catalog::text("combat", &["daylight", "water_generic"]);
        // Pythonは水中の文に現在の敵全体の個数を添える。過去の視認は混ぜない。
        let mut suffix = String::new();
        if e.visual_threats.len() >= 2 {
            let summary = if e.visual_threats.len() >= 9
                && !e.visual_threats.iter().any(|t| is_boss(&t.r#type))
            {
                massive(e, Mode::Alert)
            } else {
                let mut counts: HashMap<String, usize> = HashMap::new();
                for t in &e.visual_threats {
                    *counts.entry(norm(&t.r#type)).or_default() += 1;
                }
                let mut ordered: Vec<_> = counts.into_iter().collect();
                ordered.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
                format!(
                    "{}おるで。",
                    ordered
                        .iter()
                        .take(3)
                        .map(|(k, n)| format!("{}{}体", label(k), n.min(&9)))
                        .collect::<Vec<_>>()
                        .join("、")
                )
            };
            suffix = format!(" {summary}");
            text.push_str(&suffix);
        }
        let mut speech = Speech::visual(
            "daylight_water",
            text,
            e.visual_threats.iter().map(identity).collect(),
        );
        speech.protect_ms = 5000;
        let details = json!({"player_name":call_name(e,s),"biome":biome_label(e),"time_phase":time_phase(e),
            "hostiles":survivors.iter().map(|t|label(&t.r#type)).collect::<Vec<_>>(),"count":survivors.len(),
            "mob_states":survivors.iter().filter_map(|t|crate::mob_environment::visual(t)).collect::<Vec<_>>(),
            "__speech_suffix":suffix});
        // The shared leaf receives species knowledge plus actual per-target state.
        speech = self.leaf_speech(speech, "daylight_water", details, 0.6);
        Some(speech)
    }

    pub fn burning(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        if !elapsed(
            now,
            self.last_burning_at,
            s.ms("burning_visual_comment_cooldown_ms"),
        ) {
            return None;
        }
        let target = e
            .visual_threats
            .iter()
            .find(|t| t.on_fire && self.new_burning.contains(&identity(t)))?;
        self.last_burning_at = Some(now);
        self.new_burning.remove(&identity(target));
        let speech = Speech::visual(
            "newly_burning_visual",
            fallback("newly_burning_visual"),
            vec![identity(target)],
        );
        let details = json!({"player_name":call_name(e,s),"hostile":label(&target.r#type),"biome":biome_label(e),
            "time_phase":time_phase(e),"distance":target.distance});
        Some(self.leaf_speech(speech, "newly_burning_visual", details, 0.72))
    }
}
