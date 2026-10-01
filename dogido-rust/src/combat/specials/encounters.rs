use super::*;

impl Specials {
    pub fn rear_ambush(
        &mut self,
        e: &GameEvent,
        now: u64,
        allow_cue: bool,
        s: &Settings,
    ) -> Option<Speech> {
        if e.event.name != EventName::ThreatApproaching
            || !elapsed(now, self.last_rear_at, s.ms("ushiro_comment_cooldown_ms"))
        {
            return None;
        }
        let target = e
            .visual_threats
            .iter()
            .filter(|t| {
                !is_ranged(&t.r#type)
                    && t.direction.horizontal == Some(H::Back)
                    && t.distance
                        .is_some_and(|d| d <= s.number("rear_warning_distance"))
                    && elapsed(
                        now,
                        self.screamed.get(&identity(t)).copied(),
                        s.ms("hostile_comment_cooldown_ms"),
                    )
            })
            .min_by(distance_order)?;
        self.last_rear_at = Some(now);
        if allow_cue {
            self.screamed.insert(identity(target), now);
        }
        let name = call_name(e, s);
        let ids = e
            .visual_threats
            .iter()
            .map(|t| {
                t.entity_id
                    .as_deref()
                    .filter(|id| !id.is_empty())
                    .unwrap_or(&t.r#type)
            })
            .collect::<Vec<_>>()
            .join(",");
        let seed = format!("{}|{}|{name}|{ids}", date(e), sequence(e));
        let classic = sha1_first(seed.as_bytes()) < 26;
        let text = catalog::text(
            "combat",
            &[
                "calls",
                if classic {
                    "classic_ushiro_call"
                } else {
                    "named_ushiro_call"
                },
            ],
        )
        .replace("{player_name}", &name);
        let mut speech = Speech::visual(
            if classic {
                "ushiro_classic"
            } else {
                "ushiro_named"
            },
            text,
            vec![identity(target)],
        );
        speech.protect_ms = 2000;
        if allow_cue {
            speech.cue_id = Some("ushiro_scream");
            speech.interrupt = true;
        }
        Some(speech)
    }

    /// 暗所本体の状態はcoreから受ける。暗いだけで前方奇襲を捏造しない。
    pub fn front_ambush(
        &mut self,
        e: &GameEvent,
        now: u64,
        dark_push_active: bool,
        occluded: bool,
        allow_cue: bool,
        s: &Settings,
    ) -> Option<Speech> {
        if !dark_push_active || !occluded {
            return None;
        }
        let previously_seen = e
            .visual_threats
            .iter()
            .any(|t| !self.fresh_visual.contains(&identity(t)));
        if !previously_seen && e.visual_threats.len() != 1 {
            return None;
        }
        let target = e
            .visual_threats
            .iter()
            .filter(|t| {
                matches!(
                    t.direction.horizontal,
                    Some(H::Front | H::FrontLeft | H::FrontRight)
                ) && t.distance.is_some_and(|d| d <= 4.0)
                    && self.fresh_visual.contains(&identity(t))
                    && !Self::water_survivor(e, t)
                    && elapsed(
                        now,
                        self.screamed.get(&identity(t)).copied(),
                        s.ms("hostile_comment_cooldown_ms"),
                    )
            })
            .min_by(distance_order)?;
        let id = identity(target);
        self.screamed.insert(id.clone(), now);
        self.fresh_visual.remove(&id);
        let text = match norm(&target.r#type).as_str() {
            "charged_creeper" => catalog::text("combat", &["calls", "charged_creeper"]),
            "warden" => "ウォーデンや！音を立てんと、そーっと離れよぉ！！".to_owned(),
            "wither" => "ウィザーやんけぇ！物陰に隠れられる場所、はよ探そっ！".to_owned(),
            "elder_guardian" => {
                "エルダーガーディアンおるやん！あのレーザーは、まともに喰うたらアカン！".to_owned()
            }
            "ender_dragon" if target.direction.vertical == Some(V::Above) => {
                "上から来るでぇ！エンダードラゴンやぁ！".to_owned()
            }
            "ender_dragon" => "エンダードラゴンや！あいつ、回り込んどるでぇ！".to_owned(),
            _ => crate::threats::visual_text(target, true),
        };
        let mut speech = Speech::visual("dark_push_forward", text, vec![id]);
        if allow_cue {
            speech.cue_id = Some("front_spawn_scream");
            speech.interrupt = true;
            speech.protect_ms = 1600;
        }
        Some(speech)
    }

    pub fn neutral_hostile(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        for target in &e.visual_threats {
            let kind = norm(&target.r#type);
            if !is_neutral(&kind)
                || !self.passive_seen.get(&kind).is_some_and(|at| {
                    now.saturating_sub(*at) <= s.ms("neutral_hostility_memory_ms")
                })
                || !elapsed(
                    now,
                    self.neutral_commented.get(&kind).copied(),
                    s.ms("neutral_turned_hostile_comment_cooldown_ms"),
                )
            {
                continue;
            }
            self.neutral_commented.insert(kind.clone(), now);
            let text = choose(
                "combat",
                &["calls", "neutral_turned_hostile_variants"],
                &format!("{}|{kind}", sequence(e)),
            )
            .replace("{player_name}", &call_name(e, s))
            .replace("{label}", &label(&kind));
            return Some(Speech::visual(
                "neutral_turned_hostile",
                text,
                vec![identity(target)],
            ));
        }
        None
    }

    pub fn flying(
        &mut self,
        e: &GameEvent,
        _now: u64,
        allow_cue: bool,
        _s: &Settings,
    ) -> Option<Speech> {
        let target = e
            .visual_threats
            .iter()
            .filter(|t| {
                norm(&t.r#type) != "ender_dragon" && self.entered_flying.contains(&identity(t))
            })
            .min_by(|a, b| {
                (a.direction.vertical != Some(V::Above))
                    .cmp(&(b.direction.vertical != Some(V::Above)))
                    .then_with(|| distance_order(a, b))
            })?;
        let id = identity(target);
        self.entered_flying.remove(&id);
        let mut speech = Speech::visual(
            "flying_hostile",
            format!("上から{}きたで！", label(&target.r#type)),
            vec![id],
        );
        if allow_cue {
            speech.cue_id = Some("spot_hostile_gasp");
        }
        Some(speech)
    }

    /// 通常の四方八方・混成・増加通知より前。到着90秒を過ぎた群れはcoreへ返す。
    pub fn warp_mass(
        &mut self,
        e: &GameEvent,
        now: u64,
        mode: Mode,
        s: &Settings,
    ) -> Option<Speech> {
        if self.mass_latched
            || elapsed(now, self.warped_at, s.ms("mass_callout_warp_window_ms"))
            || e.visual_threats.is_empty()
            || ground_count(e, s) < s.number("hostile_mass_callout_threshold") as usize
        {
            return None;
        }
        self.mass_latched = true;
        Some(Speech::visual(
            "warp_mass_hostiles",
            massive(e, mode),
            e.visual_threats.iter().map(identity).collect(),
        ))
    }

    /// callerは会話優先muteを先に検査する。脅威中は期限を消費せず待つ。
    pub fn overworld_return(&mut self, e: &GameEvent, now: u64, _s: &Settings) -> Option<Speech> {
        let ready = self.return_ready_at?;
        if now < ready || !e.visual_threats.is_empty() || !e.auditory_threats.is_empty() {
            return None;
        }
        self.return_ready_at = None;
        Some(Speech {
            scope: Scope::Safe,
            ..Speech::new("overworld_return", "オーバーワールドは落ち着くな・・・")
        })
    }
}
