use super::*;
use crate::events::PassiveMob;
fn villager(m: &PassiveMob) -> bool {
    catalog::norm(&m.r#type) == "villager"
}
fn profession(m: &PassiveMob) -> String {
    catalog::norm(m.profession.as_deref().unwrap_or(""))
}
fn known_profession(p: &str) -> bool {
    [
        "none",
        "nitwit",
        "armorer",
        "butcher",
        "cartographer",
        "cleric",
        "farmer",
        "fisherman",
        "fletcher",
        "leatherworker",
        "librarian",
        "mason",
        "shepherd",
        "toolsmith",
        "weaponsmith",
    ]
    .contains(&p)
}
pub(super) fn schedule(e: &GameEvent, m: &PassiveMob) -> &'static str {
    let Some(time) = surroundings::overworld(e)
        .then_some(e.world.time_of_day)
        .flatten()
    else {
        return "wander";
    };
    let t = time.rem_euclid(24000);
    if t >= 12000 {
        return "sleep";
    }
    if t >= 11000 {
        return "wander";
    }
    if m.is_baby == Some(true) {
        if t >= 10000 || (2000..6000).contains(&t) {
            "play"
        } else {
            "wander"
        }
    } else if t >= 9000 {
        "gather"
    } else if t >= 2000
        && !["", "unknown", "unregistered", "-", "none", "nitwit"].contains(&profession(m).as_str())
    {
        "work"
    } else {
        "wander"
    }
}
fn mob_key(m: &PassiveMob) -> String {
    if !villager(m) {
        return catalog::norm(&m.r#type);
    }
    if m.is_baby == Some(true) {
        return "villager:baby".into();
    }
    format!(
        "villager:{}",
        m.profession
            .as_deref()
            .unwrap_or("none")
            .trim()
            .to_lowercase()
    )
}
pub(super) fn fallback_candidates(e: &GameEvent, m: &PassiveMob) -> Vec<String> {
    let catalog = catalog::reactions();
    let entry = &catalog["mobs"][&m.r#type];
    let mut out = vec![];
    let ids: Vec<_> = e
        .inventory
        .iter()
        .filter(|(_, n)| **n > 0)
        .map(|(k, _)| k.rsplit(':').next().unwrap_or(k).trim().to_lowercase())
        .collect();
    if let Some(rows) = entry["conditional_lines"].as_array() {
        for row in rows {
            if row["requires_inventory_any"].as_array().is_some_and(|a| {
                !a.is_empty()
                    && !a
                        .iter()
                        .any(|v| ids.iter().any(|id| v.as_str() == Some(id)))
            }) {
                continue;
            }
            let temper = row["requires_temperament"]
                .as_str()
                .unwrap_or("")
                .trim()
                .to_lowercase();
            if !temper.is_empty()
                && temper != m.temperament.as_deref().unwrap_or("").trim().to_lowercase()
            {
                continue;
            }
            if row["requires_caution_any"].as_array().is_some_and(|a| {
                !a.is_empty()
                    && !a.iter().any(|v| {
                        v.as_str().is_some_and(|s| {
                            s.trim().eq_ignore_ascii_case(
                                m.caution_reason.as_deref().unwrap_or("").trim(),
                            )
                        })
                    })
            }) {
                continue;
            }
            if let Some(line) = row["line"]
                .as_str()
                .map(str::trim)
                .filter(|s| !s.is_empty())
            {
                out.push(line.to_owned());
            }
        }
    }
    if let Some(rows) = entry["specific_lines"].as_array() {
        for v in rows {
            if let Some(s) = v.as_str().map(str::trim).filter(|s| !s.is_empty()) {
                out.push(s.to_owned());
            }
        }
    }
    let generic = if m
        .temperament
        .as_deref()
        .unwrap_or("")
        .trim()
        .eq_ignore_ascii_case("neutral")
    {
        "generic_neutral_templates"
    } else {
        "generic_templates"
    };
    if let Some(rows) = catalog[generic].as_array() {
        for v in rows {
            if let Some(s) = v.as_str() {
                out.push(
                    s.replace("{mob}", &catalog::mob_label(&m.r#type))
                        .trim()
                        .to_owned(),
                );
            }
        }
    }
    let mut unique = vec![];
    for s in out {
        if !s.is_empty() && !unique.contains(&s) {
            unique.push(s);
        }
    }
    unique
}
impl Ambient {
    pub(super) fn mob_action(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        let commentable = |mob: &&PassiveMob| !mob.identity.as_ref().is_some_and(|i| i.tamed);
        let ready = |key: &str| {
            elapsed(
                now,
                self.mob_comments.get(key).copied(),
                s.ms("ambient_mob_comment_cooldown_ms"),
            )
        };
        let awake: Vec<_> = e
            .passive_mobs
            .iter()
            .filter(|m| villager(m) && schedule(e, m) != "sleep")
            .collect();
        let crowd = awake.len() >= s.ms("ambient_villager_crowd_threshold").max(1) as usize;
        let crowd_ready = ready("villager:crowd");
        let target = if crowd || !crowd_ready {
            if crowd && crowd_ready {
                awake.into_iter().min_by(|a, b| {
                    a.distance
                        .unwrap_or(f64::INFINITY)
                        .total_cmp(&b.distance.unwrap_or(f64::INFINITY))
                })
            } else {
                e.passive_mobs
                    .iter()
                    .filter(commentable)
                    .find(|m| !villager(m) && !mob_key(m).is_empty() && ready(&mob_key(m)))
            }
        } else {
            e.passive_mobs.iter().filter(commentable).find(|m| {
                !mob_key(m).is_empty()
                    && (!villager(m) || schedule(e, m) != "sleep")
                    && ready(&mob_key(m))
            })
        }?;
        let candidates = fallback_candidates(e, target);
        let text = candidates.first()?.clone();
        let crowd = villager(target) && crowd;
        let baby = target.is_baby == Some(true) && !crowd;
        let p = profession(target);
        let prof =
            (villager(target) && !crowd && !baby && known_profession(&p)).then_some(p.as_str());
        let entry = catalog::mob(&target.r#type, prof, baby);
        let label = if crowd {
            "村人".to_owned()
        } else {
            entry["label"]
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| catalog::mob_label(&target.r#type))
        };
        let mut details = common_details(e, s);
        let fields = json!({"mob":label,"direction":direction(target),"mob_count":if crowd{1}else{e.passive_mobs.iter().filter(commentable).count()},"distance":target.distance,
            "mob_tags":catalog::tags(&entry),"mob_role":entry["poetic"]["role"].as_str().unwrap_or(""),
            "mob_temperament":target.temperament.as_deref().filter(|t|!t.is_empty()).unwrap_or("friendly"),"mob_caution_reason":target.caution_reason.as_deref().unwrap_or(""),
            "fallback_candidates":candidates,"variation_slot":e.sequence.unwrap_or(0)%4,
            "__ambient_guard":{"mob_type":target.r#type,"entity_id":target.identity.as_ref().map(|i|&i.entity_id),"profession":if crowd{None}else{prof},"baby":baby,"crowd":crowd}});
        details.as_object_mut()?.extend(fields.as_object()?.clone());
        if villager(target) {
            let activity = schedule(e, target);
            details["mob_is_baby"] = baby.into();
            details["villager_schedule"] = activity.into();
            details["villager_schedule_ja"] = match activity {
                "work" => "仕事中",
                "gather" => "集会中",
                "play" => "遊び中",
                "sleep" => "睡眠中",
                _ => "散歩中",
            }
            .into();
            if let Some(p) = prof {
                details["mob_profession"] = p.into();
                if let Some(site) = entry["job_site"].as_str() {
                    details["mob_job_site"] = site.into();
                }
            }
        }
        self.mob_comments.insert(
            if crowd {
                "villager:crowd".into()
            } else {
                mob_key(target)
            },
            now,
        );
        Some(leaf("ambient", text, details, 0.48))
    }
}
fn direction(m: &PassiveMob) -> &'static str {
    use crate::events::HorizontalDirection::*;
    match m.direction.horizontal {
        Some(Front) => "前",
        Some(FrontRight) => "右前",
        Some(Right) => "右",
        Some(BackRight) => "右後ろ",
        Some(Back) => "後ろ",
        Some(BackLeft) => "左後ろ",
        Some(Left) => "左",
        Some(FrontLeft) => "左前",
        None => "近く",
    }
}
impl Ambient {
    pub(super) fn mob_action_if_allowed(
        &mut self,
        e: &GameEvent,
        now: u64,
        mode: Mode,
        f: &AmbientFocus,
        s: &Settings,
    ) -> Option<Speech> {
        if f.boss_presence || f.ominous_presence {
            return None;
        }
        if !matches!(
            e.event.name,
            EventName::AmbientMobDetected | EventName::StatusSnapshot
        ) {
            return None;
        }
        let biome = e.world.biome.as_deref().unwrap_or("");
        let cave = biome == "deep_dark" || biome.ends_with("_caves");
        if e.world
            .weather
            .as_ref()
            .is_some_and(|w| catalog::enum_text(w) == "thunder")
            && !cave
        {
            return None;
        }
        if e.event.name == EventName::StatusSnapshot
            && (mode != Mode::Normal
                || e.combat.combat_active_hint == Some(true)
                || !elapsed(now, self.last_danger, s.ms("hostile_comment_cooldown_ms")))
        {
            return None;
        }
        self.mob_action(e, now, s)
    }
}
