//! 群れの優先順位と文面。数は現在のvisualだけから取り、過去の観測を加算しない。
use super::*;
use crate::events::EventTime;
use std::cmp::Ordering;

pub(super) fn ordinary(t: &VisualThreat) -> bool {
    matches!(
        t.r#type.as_str(),
        "zombie"
            | "zombie_villager"
            | "skeleton"
            | "spider"
            | "cave_spider"
            | "husk"
            | "stray"
            | "creeper"
            | "charged_creeper"
    ) && !t.on_fire
        && !t.in_water
}
fn counts(e: &GameEvent) -> Vec<(String, usize)> {
    let mut counts = HashMap::new();
    for t in &e.visual_threats {
        *counts.entry(t.r#type.clone()).or_insert(0) += 1;
    }
    let mut counts: Vec<_> = counts.into_iter().collect();
    counts.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    counts
}
fn within_ten(e: &GameEvent) -> usize {
    e.combat
        .hostiles_within_10
        .map(|n| n as usize)
        .unwrap_or_else(|| {
            e.visual_threats
                .iter()
                .filter(|t| t.distance.is_some_and(|d| d <= 10.0))
                .count()
        })
}
fn other_realm(e: &GameEvent, s: &Settings) -> bool {
    let dim = e
        .player
        .dimension
        .as_deref()
        .unwrap_or("")
        .trim()
        .to_lowercase();
    !matches!(dim.as_str(), "" | "overworld" | "minecraft:overworld")
        && (e.visual_threats.len() >= s.other_realm_swarm_visual_threshold
            || !e.visual_threats.is_empty()
                && e.auditory_threats.len() >= s.other_realm_audio_generic_threshold)
}
fn priority(a: &VisualThreat, b: &VisualThreat) -> Ordering {
    let key = |t: &VisualThreat| {
        let d = t.distance.unwrap_or(f64::INFINITY);
        let range = CATALOG
            .effective_range
            .get(&t.r#type)
            .copied()
            .unwrap_or(3.0);
        (
            d > 4.0,
            CATALOG.ranged.contains(&t.r#type) || d > 4.5,
            d > range,
            d / range.max(0.1),
            !rear(t),
            !t.approaching,
        )
    };
    let (a0, a1, a2, a3, a4, a5) = key(a);
    let (b0, b1, b2, b3, b4, b5) = key(b);
    a0.cmp(&b0)
        .then(a1.cmp(&b1))
        .then(a2.cmp(&b2))
        .then(a3.total_cmp(&b3))
        .then(a4.cmp(&b4))
        .then(a5.cmp(&b5))
        .then(a.r#type.cmp(&b.r#type))
}
fn overwhelmed(e: &GameEvent, preferred: &[String]) -> (String, Vec<String>) {
    // この段階の通常敵のうち、射撃役または爆発役を先に伝える。
    let mut support: Vec<_> = e
        .visual_threats
        .iter()
        .filter(|t| {
            CATALOG.ranged.contains(&t.r#type)
                || matches!(t.r#type.as_str(), "creeper" | "charged_creeper")
        })
        .collect();
    support.sort_by(|a, b| {
        priority(a, b).then_with(|| {
            let rank = |t: &VisualThreat| {
                preferred
                    .iter()
                    .position(|id| id == &identity(t))
                    .unwrap_or(usize::MAX)
            };
            rank(a).cmp(&rank(b))
        })
    });
    let mut seen = HashSet::new();
    support.retain(|t| seen.insert(t.r#type.as_str()));
    let ids = support.iter().take(2).map(|t| identity(t)).collect();
    let text = if support.is_empty() {
        "あかんあかんあかん！もうあかん！四方八方敵やんけ！俺もう終わりや〜！".into()
    } else {
        let parts = support
            .iter()
            .take(2)
            .map(|t| format!("{}に{}", direction(t), CATALOG.labels[&t.r#type]))
            .collect::<Vec<_>>()
            .join("、");
        format!("あかんあかんあかん！もうあかん！四方八方敵やんけ！ {parts}おる！")
    };
    (text, ids)
}
fn massive(e: &GameEvent) -> String {
    // Python datetime.isoformatと同じ6桁の小数・UTC offsetで既存の文面を選ぶ。
    let date = match &e.observed_at {
        EventTime::Aware(t) => format!(
            "{}{}{}",
            t.format("%Y-%m-%dT%H:%M:%S"),
            if t.timestamp_subsec_micros() == 0 {
                String::new()
            } else {
                format!(".{:06}", t.timestamp_subsec_micros())
            },
            t.format("%:z")
        ),
        EventTime::Naive(t) => format!(
            "{}{}",
            t.format("%Y-%m-%dT%H:%M:%S"),
            if t.and_utc().timestamp_subsec_micros() == 0 {
                String::new()
            } else {
                format!(".{:06}", t.and_utc().timestamp_subsec_micros())
            }
        ),
    };
    let seed = format!(
        "{}|{date}|{}|{}",
        e.sequence
            .filter(|n| *n != 0)
            .map(|n| n.to_string())
            .unwrap_or_default(),
        e.player
            .dimension
            .as_deref()
            .unwrap_or("")
            .trim()
            .to_lowercase(),
        e.world.biome.as_deref().unwrap_or("").trim().to_lowercase()
    );
    let lines = [
        "敵がぎょうさんおるで！",
        "ちょ、あかんあかん！敵がわらわら湧いとるっ！",
        "なんなんこれ、ぎょうさん湧いてお祭りかいな！勘弁してえな！",
        "ちょっと。敵おおすぎひん！？俺もう帰りたいわ……",
    ];
    lines[seed.chars().map(|c| c as usize).sum::<usize>() % lines.len()].into()
}
fn render(
    e: &GameEvent,
    s: &Settings,
    increase: Option<&str>,
    previous: Option<&Warning>,
) -> Option<Warning> {
    if e.visual_threats.len() < 2 || !e.visual_threats.iter().all(ordinary) {
        return None;
    }
    let counts = counts(e);
    let mut sequence = Vec::new();
    let mut group_support = Vec::new();
    let (kind, text) = if let Some(t) = increase {
        (
            "hostile_increase",
            format!("{}が増えたで！", CATALOG.labels[t]),
        )
    } else if e.visual_threats.len() >= 4 {
        if e.visual_threats.len() >= 9 || other_realm(e, s) {
            ("hostile_massive", massive(e))
        } else {
            let (text, targets) = overwhelmed(
                e,
                previous.map(|w| w.group_support.as_slice()).unwrap_or(&[]),
            );
            group_support = targets;
            ("hostile_overwhelmed", text)
        }
    } else if counts.len() >= 2
        || within_ten(e) >= 2
        || previous.is_some_and(|w| w.kind == "hostile_count")
    {
        let mut parts = Vec::new();
        for (t, n) in counts.iter().take(3) {
            parts.push(format!("{}{n}体", CATALOG.labels[t]));
            sequence.extend([format!("mob/{t}"), format!("common/counts/{n}")]);
        }
        sequence.push("common/phrases/orude".into());
        ("hostile_count", format!("{}おるで。", parts.join("、")))
    } else {
        return None;
    };
    Some(Warning {
        target: "visual_group".into(),
        hostile_type: increase.unwrap_or("").into(),
        horizontal: None,
        kind,
        text,
        cue: None,
        cue_sequence: sequence,
        group_counts: counts,
        group_support,
    })
}

/// 通常通知のcooldownとは別に、発声中の古い数・種類・方向を現在観測へ置換する。
/// 順序や数の変わらない移動では数の音声を切らない。増加文は構成変化後に再利用しない。
pub(super) fn refresh(w: &Warning, e: &GameEvent, s: &Settings) -> Option<Warning> {
    if let [t] = e.visual_threats.as_slice() {
        if !ordinary(t) {
            return None;
        }
        return Some(Warning {
            target: identity(t),
            hostile_type: t.r#type.clone(),
            horizontal: t.direction.horizontal,
            kind: "visual_hostile",
            text: visual_text(t, is_panic(t, e, s)),
            cue: None,
            cue_sequence: Vec::new(),
            group_counts: Vec::new(),
            group_support: Vec::new(),
        });
    }
    let same_counts = counts(e) == w.group_counts;
    let increase = (w.kind == "hostile_increase" && same_counts).then_some(w.hostile_type.as_str());
    let mut next = render(e, s, increase, Some(w))?;
    if next.kind == "hostile_massive" && w.kind == next.kind {
        // 観測時刻ごとに同義の台詞へ変わって音声を再起動しない。
        next.text = w.text.clone();
    }
    Some(next)
}

impl Policy {
    pub(super) fn group(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Warning> {
        if self.cue_allowed(e, now, s)
            && e.visual_threats.iter().any(|t| {
                self.seen.contains_key(&identity(t))
                    || self.commented.contains_key(&identity(t))
                    || self.screamed.contains_key(&identity(t))
            })
        {
            let close = e
                .visual_threats
                .iter()
                .filter(|t| {
                    let id = identity(t);
                    t.distance.is_some_and(|d| d <= 3.0)
                        && !self.seen.contains_key(&id)
                        && !self.commented.contains_key(&id)
                        && !self.screamed.contains_key(&id)
                })
                .min_by(|a, b| a.distance.unwrap().total_cmp(&b.distance.unwrap()));
            if let Some(t) = close {
                self.screamed.insert(identity(t), now);
                return Some(self.plan(
                    t,
                    "close_ambush",
                    String::new(),
                    Some(("panic_scream_start", "きゃー！")),
                    e,
                    now,
                    s,
                ));
            }
        }
        let increase = self.last_single.as_ref().and_then(|(kind, at)| {
            if elapsed(now, Some(*at), s.multi_hostile_comment_cooldown_ms) {
                return None;
            }
            let ids: HashSet<_> = e
                .visual_threats
                .iter()
                .filter(|t| &t.r#type == kind)
                .map(identity)
                .collect();
            (e.visual_threats
                .iter()
                .filter(|t| &t.r#type == kind)
                .count()
                >= 2
                && !ids.is_subset(&self.increase_ids))
            .then_some((kind.clone(), ids))
        });
        if increase.is_none()
            && !elapsed(now, self.last_callout, s.multi_hostile_comment_cooldown_ms)
        {
            return None;
        }
        let Some(mut plan) = render(e, s, increase.as_ref().map(|(kind, _)| kind.as_str()), None)
        else {
            let target = e
                .visual_threats
                .iter()
                .filter(|t| !self.commented.contains_key(&identity(t)))
                .min_by(|a, b| priority(a, b))?;
            return self.single(target, e, now, s);
        };
        if let Some((_, ids)) = increase {
            self.increase_ids.extend(ids);
        }
        self.last_single = None;
        self.last_callout = Some(now);
        let ground = ground_count(e, s);
        let gasp =
            within_ten(e) >= 2 || ground >= s.hostile_mass_callout_threshold || other_realm(e, s);
        if (gasp
            || e.visual_threats
                .iter()
                .min_by(|a, b| priority(a, b))
                .is_some_and(|t| spotted_gasp(t, s)))
            && self.cue_allowed(e, now, s)
        {
            self.last_cue = Some(now);
            plan.cue = Some(Cue {
                id: "spot_hostile_gasp",
                text: if gasp { "ひいっ！" } else { "ハッ" },
                file: "panic/freesound_community-male-gasp-1-7183.mp3",
            });
        }
        Some(plan)
    }
}

impl Warning {
    /// 素材が全て揃う場合だけ使用する。欠けた断片の前半を再生してから本文を重ねない。
    pub fn fragment_paths(&self, directory: &std::path::Path) -> Option<Vec<PathBuf>> {
        if self.cue_sequence.is_empty() {
            return None;
        }
        self.cue_sequence
            .iter()
            .map(|id| {
                ["mp3", "wav", "m4a"]
                    .into_iter()
                    .map(|ext| directory.join(format!("{id}.{ext}")))
                    .find(|p| p.is_file())
            })
            .collect()
    }
}

pub(super) fn has_report(e: &GameEvent, s: &Settings) -> bool {
    render(e, s, None, None).is_some()
}

fn ground_count(e: &GameEvent, s: &Settings) -> usize {
    e.combat
        .hostiles_within_scan_ground
        .or(e.combat.hostiles_within_30_ground)
        .map(|n| n as usize)
        .unwrap_or_else(|| {
            e.visual_threats
                .iter()
                .filter(|t| {
                    !matches!(
                        t.r#type.as_str(),
                        "blaze" | "ender_dragon" | "ghast" | "phantom" | "vex" | "wither"
                    ) && t.distance.is_some_and(|d| d <= s.hostile_query_distance)
                })
                .count()
        })
}
impl Policy {
    pub(super) fn update_group_presence(&mut self, e: &GameEvent, s: &Settings) {
        use crate::events::EventName;
        if matches!(
            e.event.name,
            EventName::StatusSnapshot
                | EventName::ThreatApproaching
                | EventName::CombatEnded
                | EventName::PlayerDied
        ) {
            let ground = ground_count(e, s);
            if ground == 0 && self.last_ground_count > 0 {
                self.last_callout = None;
                self.last_single = None;
                self.increase_ids.clear();
            }
            self.last_ground_count = ground;
        }
    }
}
