use super::*;
use crate::events::{
    CardinalDirection, SmellDirectionEstimateVertical, SmellObservation,
    SmellObservationCategory as Category, SmellObservationSmellId as Id,
    SmellObservationStatus as Status, SmellObservationSuppressionReason as Reason,
};
#[derive(Clone, Default)]
pub(super) struct Presence {
    active: Option<String>,
    pending: Option<String>,
    count: u8,
    // A handled opportunity includes an eventual model choice to stay silent.
    // Actual speech is recorded only by the delivery runtime.
    considered: Option<String>,
    last: Option<u64>,
    spatial_retry_used: bool,
    spatial_retry: Option<(Value, u8)>,
}
pub(super) fn observation(e: &GameEvent) -> Option<SmellObservation> {
    e.smell_observation.clone()
}
fn signature(e: &GameEvent) -> Option<String> {
    let o = observation(e)?;
    if o.status != Status::Present {
        return None;
    }
    Some(
        [
            catalog::enum_text(&o.specificity),
            catalog::enum_text(&o.smell_id),
            catalog::enum_text(&o.category),
            catalog::enum_text(&o.valence),
        ]
        .join(":"),
    )
}
impl Presence {
    pub fn update(&mut self, e: &GameEvent) {
        if let Some((previous, count)) = &mut self.spatial_retry {
            let current = spatial_key(e);
            if *previous == current {
                *count = count.saturating_add(1);
            } else {
                *previous = current;
                *count = 1;
            }
        }
        let signature = signature(e);
        if e.smell_observation.is_none() {
            return;
        }
        if signature == self.active {
            self.pending = None;
            self.count = 0;
            return;
        }
        let next = signature.clone().unwrap_or_else(|| "__absent__".into());
        if self.pending.as_ref() == Some(&next) {
            self.count = self.count.saturating_add(1);
        } else {
            self.pending = Some(next);
            self.count = 1;
        }
        if self.count >= 2 {
            self.active = signature;
            self.pending = None;
            self.count = 0;
            self.considered = None;
            self.spatial_retry_used = false;
            self.spatial_retry = None;
        }
    }
    // Only a cancelled, unplayed spatial estimate releases this opportunity.
    // One immediate retry is allowed per presence; further cancellations retain
    // the normal cooldown. A stable second sample is required in either case.
    pub fn reconsider_spatial(&mut self, e: &GameEvent) {
        if signature(e).is_none() || signature(e) != self.active {
            return;
        }
        self.considered = None;
        self.spatial_retry = Some((spatial_key(e), 1));
        if !self.spatial_retry_used {
            self.last = None;
            self.spatial_retry_used = true;
        }
    }
    pub fn mark(&mut self, e: &GameEvent, now: u64) {
        if let Some(signature) = signature(e) {
            self.active = Some(signature.clone());
            self.considered = Some(signature);
            self.pending = None;
            self.count = 0;
            self.last = Some(now);
            self.spatial_retry = None;
        }
    }
    pub fn action(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        let sig = signature(e)?;
        if self.active.as_ref() != Some(&sig)
            || self.considered.as_ref() == Some(&sig)
            || !elapsed(now, self.last, s.ms("smell_comment_cooldown_ms"))
            || self
                .spatial_retry
                .as_ref()
                .is_some_and(|(_, count)| *count < 2)
        {
            return None;
        }
        self.mark(e, now);
        let mut line = speech(e);
        line.scope = Scope::Safe;
        line.protect_ms = 2000;
        Some(line)
    }
}
fn spatial_key(e: &GameEvent) -> Value {
    let observation = observation(e);
    json!(
        observation
            .as_ref()
            .and_then(|o| o.direction_estimate.as_ref())
    )
}
pub(crate) fn is_query(text: &str) -> bool {
    let text = text.replace(' ', "");
    if [
        "言葉",
        "ことば",
        "句",
        "川柳",
        "俳句",
        "表現",
        "文章",
        "文体",
        "作品",
        "物語",
        "詩",
        "比喩",
        "ニュアンス",
    ]
    .iter()
    .any(|v| text.contains(v))
    {
        return false;
    }
    let nearby = |prefixes: &[&str], width: usize, tails: &[&str]| -> bool {
        prefixes.iter().any(|p| {
            text.match_indices(p).any(|(i, _)| {
                let rest = &text[i + p.len()..];
                rest.char_indices()
                    .map(|(n, _)| n)
                    .chain(std::iter::once(rest.len()))
                    // Python regex dot does not cross a typed newline.
                    .take_while(|n| !rest[..*n].contains('\n'))
                    .take(width + 1)
                    .any(|n| tails.iter().any(|t| rest[n..].starts_with(t)))
            })
        })
    };
    nearby(
        &["この", "その", "あの", "今の", "さっきの", "ここ", "ここの"],
        6,
        &["匂", "臭", "にお", "香り"],
    ) || nearby(&["何", "なに", "なん"], 5, &["匂", "臭", "にお", "香り"])
        || ["匂い", "におい", "臭い", "香り"].iter().any(|p| {
            text.match_indices(p).any(|(i, _)| {
                let rest = &text[i + p.len()..];
                ["", "って", "は", "が", "の"].iter().any(|particle| {
                    rest.strip_prefix(particle).is_some_and(|rest| {
                        [
                            "何",
                            "なに",
                            "なん",
                            "する",
                            "した",
                            "して",
                            "来",
                            "きた",
                            "漂",
                            "残",
                            "かも",
                            "かな",
                            "やろ",
                            "どこ",
                            "どっち",
                            "どちら",
                            "どの方向",
                            "方向",
                            "方角",
                            "距離",
                            "どのくらい",
                            "どれくらい",
                            "近い",
                            "遠い",
                        ]
                        .iter()
                        .any(|tail| rest.starts_with(tail))
                    })
                })
            })
        })
        || nearby(&["匂", "臭", "にお"], 0, &["う", "って", "った"])
        || nearby(&["なんか", "何か"], 5, &["臭い", "くさい"])
        || text.contains("くさっ")
        || text.contains("臭っ")
        || nearby(
            &["どこ", "どっち", "どちら", "どの方向", "どれくらい"],
            8,
            &["匂", "臭", "にお"],
        )
}
pub(super) fn speech(e: &GameEvent) -> Speech {
    let mut reply = base_speech(e);
    let Some(observation) = observation(e) else {
        return reply;
    };
    if let Some(direction) = observation.direction_estimate {
        let cardinal = direction.cardinal.map(|value| match value {
            CardinalDirection::North => "北",
            CardinalDirection::Northeast => "北東",
            CardinalDirection::East => "東",
            CardinalDirection::Southeast => "南東",
            CardinalDirection::South => "南",
            CardinalDirection::Southwest => "南西",
            CardinalDirection::West => "西",
            CardinalDirection::Northwest => "北西",
        });
        let vertical = direction.vertical.map(|value| match value {
            SmellDirectionEstimateVertical::Above => "上",
            SmellDirectionEstimateVertical::Below => "下",
        });
        let bearing = [cardinal, vertical]
            .into_iter()
            .flatten()
            .collect::<Vec<_>>()
            .join("の");
        reply
            .text
            .push_str(&format!("匂いは{bearing}のほうから来とるみたいや。"));
        reply.cue_id = None;
    }
    reply
}

pub(super) fn query_reply(e: &GameEvent, text: &str) -> Speech {
    let mut reply = speech(e);
    let Some(observation) = observation(e).filter(|o| o.status == Status::Present) else {
        return reply;
    };
    if observation.direction_estimate.is_none()
        && ["どこ", "どっち", "どちら", "方向", "方角"]
            .iter()
            .any(|word| text.contains(word))
    {
        reply.text.push_str("方向はまだ絞れてへんわ。");
        reply.cue_id = None;
    }
    if ["距離", "どれくらい", "どのくらい", "近い", "遠い"]
        .iter()
        .any(|word| text.contains(word))
    {
        reply.text.push_str("距離までは分からへんわ。");
        reply.cue_id = None;
    }
    reply
}

pub(super) fn still_applicable(reply: &Speech, e: &GameEvent) -> bool {
    let current = speech(e);
    if current.text == reply.text {
        return true;
    }
    let Some(observation) = observation(e).filter(|o| o.status == Status::Present) else {
        return false;
    };
    let direction = if observation.direction_estimate.is_none() {
        "方向はまだ絞れてへんわ。"
    } else {
        ""
    };
    let distance = "距離までは分からへんわ。";
    [
        direction.to_owned(),
        distance.to_owned(),
        format!("{direction}{distance}"),
    ]
    .iter()
    .any(|suffix| !suffix.is_empty() && reply.text == format!("{}{suffix}", current.text))
}

fn base_speech(e: &GameEvent) -> Speech {
    let Some(o) = observation(e) else {
        return cue(
            "smell_unsupported",
            catalog::general("chat", "no_scent_evidence"),
        );
    };
    if o.status == Status::None {
        return cue("smell_none", catalog::general("smell", "none"));
    }
    if o.status == Status::Suppressed {
        return if o.suppression_reason == Some(Reason::Submerged) {
            cue(
                "smell_suppressed_submerged",
                catalog::general("smell", "suppressed_submerged"),
            )
        } else {
            cue(
                "smell_suppressed_weather",
                catalog::general("smell", "suppressed_weather"),
            )
        };
    }
    if o.smell_id == Some(Id::Zombie) {
        return cue(
            "zombie_scent_warning",
            catalog::general("combat", "zombie_scent_nearby"),
        );
    }
    let id = o
        .smell_id
        .as_ref()
        .map(catalog::enum_text)
        .unwrap_or_else(|| "mixed".into());
    let key = if [
        "rotten_flesh",
        "composter",
        "brewing_stand",
        "swamp",
        "raw_meat",
        "raw_fish",
        "cooked_meat",
        "cooked_fish",
        "cooking_meat",
        "cooking_fish",
        "soup",
        "cookie",
        "cake",
        "bread",
        "ink_sac",
        "rain_after",
        "decay",
        "food",
        "mixed",
    ]
    .contains(&id.as_str())
    {
        id
    } else if o.category == Some(Category::Flower) {
        format!(
            "flower_{}",
            o.valence
                .as_ref()
                .map(catalog::enum_text)
                .unwrap_or_else(|| "mixed".into())
        )
    } else if matches!(
        o.category,
        Some(Category::Decay | Category::Food | Category::RainAfter)
    ) {
        catalog::enum_text(&o.category)
    } else {
        "mixed".into()
    };
    let cid = match key.as_str() {
        "rotten_flesh" => "smell_rotten_flesh",
        "composter" => "smell_composter",
        "brewing_stand" => "smell_brewing_stand",
        "swamp" => "smell_swamp",
        "raw_meat" => "smell_raw_meat",
        "raw_fish" => "smell_raw_fish",
        "cooked_meat" => "smell_cooked_meat",
        "cooked_fish" => "smell_cooked_fish",
        "cooking_meat" => "smell_cooking_meat",
        "cooking_fish" => "smell_cooking_fish",
        "soup" => "smell_soup",
        "cookie" => "smell_cookie",
        "cake" => "smell_cake",
        "bread" => "smell_bread",
        "ink_sac" => "smell_ink_sac",
        "rain_after" => "smell_rain_after",
        "decay" => "smell_decay",
        "food" => "smell_food",
        "flower_pleasant" => "smell_flower_pleasant",
        "flower_unpleasant" => "smell_flower_unpleasant",
        "flower_mixed" => "smell_flower_mixed",
        _ => "smell_mixed",
    };
    cue(cid, catalog::general("smell", &key))
}
fn cue(id: &'static str, text: String) -> Speech {
    let mut s = Speech::new("smell", text);
    s.cue_id = Some(id);
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    fn event(cardinal: &str) -> GameEvent {
        GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture","sequence":1,
            "observed_at":"2026-10-01T00:00:00Z","event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{},"world":{},"smell_observation":{"status":"present","smell_id":"zombie",
            "category":"decay","valence":"unpleasant","specificity":"source","source_kind":"entity","effective_strength":5,
            "direction_estimate":{"cardinal":cardinal}}})).unwrap()
    }
    #[test]
    fn cancelled_spatial_estimate_gets_one_stable_retry_then_normal_cooldown() {
        let settings = Settings::default();
        let mut presence = Presence::default();
        let first = event("east");
        presence.update(&first);
        presence.update(&first);
        assert!(presence.action(&first, 1000, &settings).is_some());
        let second = event("west");
        presence.reconsider_spatial(&second);
        assert!(presence.action(&second, 1100, &settings).is_none());
        presence.update(&second);
        assert!(presence.action(&second, 1200, &settings).is_some());
        let third = event("north");
        presence.reconsider_spatial(&third);
        presence.update(&third);
        assert!(presence.action(&third, 1300, &settings).is_none());
        assert!(
            presence
                .action(
                    &third,
                    1200 + settings.ms("smell_comment_cooldown_ms"),
                    &settings
                )
                .is_some()
        );
        // Completion or an explicit silent choice consumes the consideration.
        presence.update(&third);
        assert!(presence.action(&third, 1_000_000, &settings).is_none());
    }
}
