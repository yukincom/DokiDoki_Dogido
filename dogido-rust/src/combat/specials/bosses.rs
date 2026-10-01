use super::*;

impl Specials {
    /// normal/alert/panic/suppressed の全経路の先頭。通常悲鳴CDを迂回する。
    pub fn sonic_boom(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        let world_boom = e.world.ominous_sound_kind.as_deref().map(norm).as_deref()
            == Some("warden_sonic_boom")
            && e.world
                .ominous_sound_recent_ms
                .is_some_and(|n| n >= 0 && n as u64 <= s.ms("warden_sonic_boom_fresh_ms"));
        let audio_boom = e.auditory_threats.iter().any(|t| {
            t.sound_event
                .as_deref()
                .is_some_and(|v| v.to_lowercase().contains("sonic_boom"))
        });
        if !(world_boom || audio_boom)
            || !elapsed(
                now,
                self.last_sonic_at,
                s.ms("warden_sonic_boom_scream_cooldown_ms"),
            )
        {
            return None;
        }
        self.last_sonic_at = Some(now);
        let mut speech = Speech::new("warden_sonic_boom", "ぎゃあああ！！");
        speech.cue_id = Some("warden_sonic_boom_scream");
        speech.interrupt = true;
        speech.protect_ms = 1500;
        Some(speech)
    }

    /// 明示個数/方向質問はcoreが先に処理する。初視認のrevealも先に通す。
    pub fn warden_tactic(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        let visible = e.visual_threats.iter().any(|t| norm(&t.r#type) == "warden");
        let recent = self.last_visual_types.contains("warden")
            && !elapsed(
                now,
                self.last_visual_at,
                s.ms("boss_recent_visual_window_ms"),
            );
        if !(visible || recent)
            || e.visual_threats
                .iter()
                .any(|t| norm(&t.r#type) == "warden" && self.fresh_boss.contains(&identity(t)))
        {
            return None;
        }
        let golems = e.combat.warden_nearby_iron_golem_count.unwrap_or(0) >= 2;
        let extreme = e.combat.warden_end_crystal_bombardment_active == Some(true)
            || e.combat.warden_tnt_minecart_setup_active == Some(true)
            || e.combat.warden_ranged_trap_active == Some(true);
        let (kind, key) = if golems && !self.warden_golems {
            self.warden_golems = true;
            ("warden_golem_army", "golem_army")
        } else if extreme && !self.warden_extreme {
            self.warden_extreme = true;
            ("warden_extreme_tactics", "extreme_tactics")
        } else if e.combat.warden_recently_hurt == Some(true)
            && !golems
            && !extreme
            && !self.warden_attack
        {
            self.warden_attack = true;
            ("warden_attack_start", "attack_start")
        } else {
            return None;
        };
        Some(Speech::new(kind, catalog::text("boss", &["warden", key])))
    }

    pub fn dragon_context(&self, e: &GameEvent, now: u64) -> bool {
        dragon_phase(e).is_some()
            || dragon_seen(e)
            || self
                .last_dragon_seen_at
                .is_some_and(|at| now.saturating_sub(at) <= 20000)
    }

    /// normalでも呼ぶ。ambient muteより先。明示質問のターンには呼ばない。
    pub fn dragon(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        if !self.dragon_context(e, now)
            || e.visual_threats.iter().any(|t| {
                norm(&t.r#type) == "ender_dragon" && self.fresh_boss.contains(&identity(t))
            })
        {
            return None;
        }
        let phase = dragon_phase(e);
        if phase.as_deref().is_some_and(is_perch) && !self.dragon_perched {
            self.dragon_perched = true;
            return Some(Speech::new(
                "dragon_perch",
                catalog::text("boss", &["ender_dragon", "chance_time"]),
            ));
        }
        if phase.as_deref() == Some("charging_player")
            && elapsed(
                now,
                self.last_dragon_charge_at,
                s.ms("dragon_approach_callout_cooldown_ms"),
            )
        {
            self.last_dragon_charge_at = Some(now);
            return Some(Speech::new(
                "dragon_charge",
                catalog::text("boss", &["ender_dragon", "approach"]),
            ));
        }
        if !self.crystal_hint && e.combat.end_crystal_count.is_some_and(|n| n > 0) {
            self.crystal_hint = true;
            self.pending_crystals = None;
            return Some(Speech::new(
                "dragon_crystal_hint",
                catalog::text("boss", &["ender_dragon", "crystal_first_hint"]).replace(
                    "{count}",
                    &e.combat
                        .end_crystal_count
                        .expect("positive count")
                        .to_string(),
                ),
            ));
        }
        if self.pending_crystals.is_some()
            && elapsed(
                now,
                self.last_crystal_at,
                s.ms("dragon_crystal_callout_cooldown_ms"),
            )
        {
            let remaining = self.pending_crystals.take().expect("pending count");
            self.last_crystal_at = Some(now);
            self.crystal_hint = true;
            let key = if remaining <= 0 {
                "crystal_clear"
            } else {
                "crystal_remaining"
            };
            return Some(Speech::new(
                "dragon_crystal_count",
                catalog::text("boss", &["ender_dragon", key])
                    .replace("{count}", &remaining.to_string()),
            ));
        }
        None
    }

    pub fn boss_visual(
        &mut self,
        e: &GameEvent,
        now: u64,
        allow_cue: bool,
        s: &Settings,
    ) -> Option<Speech> {
        let target = e
            .visual_threats
            .iter()
            .filter(|t| is_boss(&t.r#type))
            .min_by(priority)?;
        let id = identity(target);
        let mob = norm(&target.r#type);
        let fresh = self.fresh_boss.contains(&id);
        if (mob == "ender_dragon" && !fresh)
            || !elapsed(
                now,
                self.boss_commented.get(&id).copied(),
                s.ms("hostile_comment_cooldown_ms"),
            )
        {
            return None;
        }
        let text = if fresh
            && matches!(
                mob.as_str(),
                "ender_dragon" | "wither" | "warden" | "elder_guardian"
            ) {
            catalog::text("boss", &[&mob, "reveal"])
        } else {
            match mob.as_str() {
                "warden" => "ウォーデンや！音を立てんと、そーっと離れよぉ！！".to_owned(),
                "wither" => "ウィザーやんけぇ！物陰に隠れられる場所、はよ探そっ！".to_owned(),
                "elder_guardian" => {
                    "エルダーガーディアンおるやん！あのレーザーは、まともに喰うたらアカン！"
                        .to_owned()
                }
                _ => crate::threats::visual_text(target, false),
            }
        };
        self.boss_commented.insert(id.clone(), now);
        let mut speech = Speech::visual("boss_visual", text, vec![id]);
        if fresh && allow_cue && matches!(mob.as_str(), "wither" | "warden" | "elder_guardian") {
            speech.cue_id = Some("boss_reveal_scream");
        }
        Some(speech)
    }

    pub fn omen(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        let kind = norm(e.world.boss_omen_kind.as_deref()?);
        let (boss, key) = match kind.as_str() {
            "ender_dragon_arena" => ("ender_dragon", "arena_hint"),
            "ender_dragon_summon" => ("ender_dragon", "summon_commit"),
            "wither_assembly" => ("wither", "assembly_hint"),
            _ => return None,
        };
        if self.last_omen.as_deref() == Some(&kind)
            && !elapsed(
                now,
                self.last_omen_at,
                s.ms("boss_omen_comment_cooldown_ms"),
            )
        {
            return None;
        }
        self.last_omen = Some(kind);
        self.last_omen_at = Some(now);
        Some(Speech::new(
            "boss_omen",
            catalog::text("boss", &[boss, key]),
        ))
    }

    pub fn mining_fatigue(&mut self, _e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        if !self.entered_fatigue
            || !elapsed(
                now,
                self.last_fatigue_at,
                s.ms("mining_fatigue_comment_cooldown_ms"),
            )
        {
            return None;
        }
        self.entered_fatigue = false;
        self.last_fatigue_at = Some(now);
        Some(Speech::new(
            "mining_fatigue",
            catalog::text("boss", &["elder_guardian", "mining_fatigue"]),
        ))
    }

    pub fn ominous(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        if self.boss_presence(now, s) || e.event.name == EventName::HostileAudioDetected {
            return None;
        }
        let kind = fresh_ominous(e, s)?;
        let cd = if matches!(kind.as_str(), "sculk_sensor" | "sculk_shrieker") {
            "sculk_ominous_sound_comment_cooldown_ms"
        } else {
            "ominous_sound_comment_cooldown_ms"
        };
        if !elapsed(now, self.ominous_comment_at, s.ms(cd)) {
            return None;
        }
        let stage = if self.ominous_stage >= 1 && severity(&kind) >= self.ominous_severity.max(2) {
            2
        } else {
            1
        };
        self.ominous_comment_at = Some(now);
        self.ominous_stage = self.ominous_stage.max(stage);
        let text = match kind.as_str() {
            "warden_heartbeat" => catalog::text(
                "boss",
                &[
                    "warden",
                    if stage <= 1 {
                        "heartbeat_first"
                    } else {
                        "heartbeat_close"
                    },
                ],
            ),
            "warden_presence" => catalog::text("boss", &["warden", "heartbeat_close"]),
            _ => choose(
                "boss",
                &[
                    "deep_dark",
                    if kind == "sculk_shrieker" {
                        "sculk_shrieker_fallbacks"
                    } else {
                        "sculk_sensor_fallbacks"
                    },
                ],
                &format!("{kind}:{}:{stage}", e.sequence.unwrap_or(0)),
            ),
        };
        let hints = ["反響", "悲鳴っぽさ", "静けさ", "嫌な予感"];
        let seed = format!("{kind}:{stage}:{}", e.sequence.unwrap_or(0));
        let details = json!({"player_name":call_name(e,s),"biome":biome_label(e),"time_phase":time_phase(e),
            "ominous_kind":kind,"ominous_stage":stage,"variation_hint":hints[seed.chars().map(|c|c as usize).sum::<usize>()%hints.len()]});
        Some(self.leaf_speech(
            Speech::new("deep_dark_ominous_sound", text),
            "deep_dark_ominous_sound",
            details,
            0.6,
        ))
    }
}
