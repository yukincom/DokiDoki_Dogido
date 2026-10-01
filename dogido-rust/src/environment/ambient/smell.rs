use super::*;
use crate::events::{
    SmellObservation, SmellObservationCategory as Category, SmellObservationSmellId as Id,
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
}
pub(super) fn observation(e: &GameEvent) -> Option<SmellObservation> {
    e.smell_observation.clone().or_else(||(!e.zombie_scent_clues.is_empty()).then(||serde_json::from_value(json!({"status":"present","smell_id":"zombie","category":"decay","valence":"unpleasant","source_kind":"entity","specificity":"source","effective_strength":8,"temperature_modifier":0})).unwrap()))
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
        let signature = signature(e);
        if signature.is_none() && e.smell_observation.is_none() {
            self.active = None;
            self.pending = None;
            self.count = 0;
            self.considered = None;
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
        let required = if e.smell_observation.is_none() && !e.zombie_scent_clues.is_empty() {
            1
        } else {
            2
        };
        if self.count >= required {
            self.active = signature;
            self.pending = None;
            self.count = 0;
            self.considered = None;
        }
    }
    pub fn mark(&mut self, e: &GameEvent, now: u64) {
        if let Some(signature) = signature(e) {
            self.active = Some(signature.clone());
            self.considered = Some(signature);
            self.pending = None;
            self.count = 0;
            self.last = Some(now);
        }
    }
    pub fn action(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        let sig = signature(e)?;
        let key = if e.smell_observation.is_none() {
            "zombie_scent_comment_cooldown_ms"
        } else {
            "smell_comment_cooldown_ms"
        };
        if self.active.as_ref() != Some(&sig)
            || self.considered.as_ref() == Some(&sig)
            || !elapsed(now, self.last, s.ms(key))
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
                            "何", "なに", "なん", "する", "した", "して", "来", "きた", "漂", "残",
                            "かも", "かな", "やろ",
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
}
pub(super) fn speech(e: &GameEvent) -> Speech {
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
