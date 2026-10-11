//! 現在の匂いに対するコード固定回答と、ゾンビ匂いの優先反応機会を管理する。
//! 種類の到着はsignature、生成済み発話の現在性は強度・発生源属性を含むestimate_keyで見る。
//! 匂いを計算するのはFabric、一般的な周期通知はnotice、実再生の確定は配送側の責務。
use super::*;
use crate::events::{
    SmellComponent, SmellObservation, SmellObservationSmellId as Id,
    SmellObservationStatus as Status, SmellObservationSuppressionReason as Reason,
};
/// 雨上がりへ一度だけ「あっ」と反応し、出力後は鼻が慣れた扱いにする。
/// 通常臭のPresenceとは分ける。雨上がりの一言でゾンビ臭の間隔まで消費しない。
#[derive(Clone, Default)]
pub(super) struct RainAfter {
    window: Option<u64>,
    finished: bool,
}
impl RainAfter {
    pub fn update(&mut self, event: &GameEvent) {
        let Some(observation) = &event.smell_observation else {
            return;
        };
        if !observation.rain_after_active {
            *self = Self::default();
        } else if let Some(notice) = &observation.rain_after_notice
            && self.window != Some(notice.window_id)
        {
            self.window = Some(notice.window_id);
            self.finished = false;
        }
        // 窓内で一時的に源が遠ざかった／水に入っただけなら、扱い済みを解除しない。
    }
    pub fn pending(&self, event: &GameEvent) -> Option<u64> {
        let observation = event.smell_observation.as_ref()?;
        let notice = observation.rain_after_notice.as_ref()?;
        (observation.rain_after_active
            && observation.status != Status::Suppressed
            && self.window == Some(notice.window_id)
            && !self.finished)
            .then_some(notice.window_id)
    }
    pub fn finish(&mut self, window: u64) {
        // 再生の成功記録ではなく、雨上がりへの一度の注意を終える。
        // 失敗・取消でも再生成を繰り返さず、通常臭へ戻る。配送結果は元のstatusに残す。
        // 遅れて届いた前の雨上がりの終端で、新しい雨上がりを消費しない。
        if self.window == Some(window) {
            self.finished = true;
        }
    }
}

#[derive(Clone, Default)]
pub(super) struct Presence {
    // 2回続けて確認できた種類集合。pending/countは切替の途中を保持する。
    active: Option<String>,
    pending: Option<String>,
    count: u8,
    // 「検討済み」にはモデルが黙る選択も含む。実際に話したことを示すフラグではない。
    // 再生成功の記録は配送側だけが確定し、同じ匂いで毎回生成し直すことをここで防ぐ。
    considered: Option<String>,
    last: Option<u64>,
    estimate_retry_used: bool,
    estimate_retry: Option<(Value, u8)>,
}
pub(super) fn observation(e: &GameEvent) -> Option<SmellObservation> {
    e.smell_observation.clone()
}
// 種類集合だけを比較する。近づいて強くなっても、
// 同じ匂いが毎フレーム新着になることはない。入力配列の並びにも依存させない。
fn signature(e: &GameEvent) -> Option<String> {
    let o = observation(e)?;
    if o.status != Status::Present {
        return None;
    }
    let mut ids: Vec<_> = o
        .components
        .iter()
        .map(|c| catalog::enum_text(&c.smell_id))
        .collect();
    ids.sort();
    Some(ids.join(":"))
}
impl Presence {
    pub fn update(&mut self, e: &GameEvent) {
        if let Some((previous, count)) = &mut self.estimate_retry {
            let current = estimate_key(e);
            if *previous == current {
                *count = count.saturating_add(1);
            } else {
                *previous = current;
                *count = 1;
            }
        }
        let signature = signature(e);
        // field未提供は「匂いが消えた」根拠ではない。明示none/suppressedと区別する。
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
            self.estimate_retry_used = false;
            self.estimate_retry = None;
        }
    }
    // 未再生の強度・発生源属性が古くなった取消だけ、この入口から検討済みを解除する。
    // 同じ種類集合につき即時の再判断は一度だけ。再取消後は通常の間隔を守る。
    // どちらの場合も新しい推定を次の観測でも確認し、瞬間的な揺れだけで発話し直さない。
    pub fn reconsider_estimate(&mut self, e: &GameEvent) {
        if signature(e).is_none() || signature(e) != self.active {
            return;
        }
        self.considered = None;
        self.estimate_retry = Some((estimate_key(e), 1));
        if !self.estimate_retry_used {
            self.last = None;
            self.estimate_retry_used = true;
        }
    }
    // 発話または沈黙を選ぶ「機会」を消費する。TTS完了を先取りして記録する処理ではない。
    pub fn mark(&mut self, e: &GameEvent, now: u64) {
        if let Some(signature) = signature(e) {
            self.active = Some(signature.clone());
            self.considered = Some(signature);
            self.pending = None;
            self.count = 0;
            self.last = Some(now);
            self.estimate_retry = None;
        }
    }
    pub fn action(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        let sig = signature(e)?;
        if self.active.as_ref() != Some(&sig)
            || self.considered.as_ref() == Some(&sig)
            || !elapsed(now, self.last, s.ms("smell_comment_cooldown_ms"))
            || self
                .estimate_retry
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
// 再判断待ちの安定確認には、種類だけでなく現在の強度・発生源属性も必要。
// 到着判定用のsignatureへ置き換えると、推定が揺れ続けていても安定した扱いになる。
fn estimate_key(e: &GameEvent) -> Value {
    super::smell_context(e).unwrap_or(Value::Null)
}

pub(super) fn label(id: Id) -> &'static str {
    match id {
        Id::Zombie => "ゾンビ系",
        Id::RottenFlesh => "腐った肉",
        Id::Composter => "コンポスター",
        Id::BrewingStand => "醸造台",
        Id::Swamp => "沼",
        Id::RawMeat => "生肉",
        Id::RawFish => "生魚",
        Id::CookedMeat => "焼いた肉",
        Id::CookedFish => "焼いた魚",
        Id::CookingMeat => "焼ける肉",
        Id::CookingFish => "焼ける魚",
        Id::Soup => "スープ",
        Id::Cookie => "クッキー",
        Id::Cake => "ケーキ",
        Id::Bread => "パン",
        Id::InkSac => "イカ墨",
        Id::RainAfter => "雨上がり",
        Id::LilyOfTheValley => "スズラン",
        Id::Lilac => "ライラック",
        Id::Peony => "ボタン",
        Id::RoseBush => "バラ",
        Id::WitherRose => "ウィザーローズ",
        Id::CactusFlower => "サボテンの花",
        Id::FloweringAzalea => "ツツジの花",
        Id::Allium => "アリウム",
        Id::PitcherPlant => "ウツボカズラ",
        Id::Torchflower => "トーチフラワー",
        Id::OpenEyeblossom => "開いたヒトミソウ",
    }
}
// 明示質問用の固定表現。モデルに渡す数値はそのまま残し、ここだけ聞きやすい強弱へ写す。
// 強いことを「近い」「何体もいる」へ変換しない。
fn component_speech(component: &SmellComponent) -> String {
    let name = label(component.smell_id);
    let strength = match component.effective_strength {
        1 => "ほのかに",
        2..=3 => "",
        4..=7 => "はっきり",
        _ => "強烈に",
    };
    format!("{name}の匂いが{strength}するで。")
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
pub(super) fn query_reply(e: &GameEvent, text: &str) -> Speech {
    let Some(observation) = observation(e).filter(|o| o.status == Status::Present) else {
        return speech(e);
    };
    query_components(&observation.components, text)
}
// 受信観測を改変せず、通常の複数成分と一度の雨上がり一成分で同じ回答規則を使う。
pub(super) fn query_components(components: &[SmellComponent], text: &str) -> Speech {
    let mut reply = speech_components(components);
    if ["どこ", "どっち", "どちら", "方向", "方角"]
        .iter()
        .any(|word| text.contains(word))
    {
        reply.text.push_str(UNKNOWN_DIRECTION);
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

const UNKNOWN_DIRECTION: &str = "匂いだけでは方向までは分からへんわ。";

// コード固定回答だけの現在性検査。同じ観測から本文を作り直し、質問用の補足も照合する。
// 自由生成された環境対話は、本文の文字列ではなくreaction側の観測guardで検査する。
pub(super) fn still_applicable(reply: &Speech, e: &GameEvent) -> bool {
    let current = speech(e);
    if current.text == reply.text {
        return true;
    }
    let Some(observation) = observation(e).filter(|o| o.status == Status::Present) else {
        return false;
    };
    components_still_applicable(reply, &observation.components)
}
pub(super) fn components_still_applicable(reply: &Speech, components: &[SmellComponent]) -> bool {
    let current = speech_components(components);
    if current.text == reply.text {
        return true;
    }
    let direction = UNKNOWN_DIRECTION;
    let distance = "距離までは分からへんわ。";
    [
        direction.to_owned(),
        distance.to_owned(),
        format!("{direction}{distance}"),
    ]
    .iter()
    .any(|suffix| !suffix.is_empty() && reply.text == format!("{}{suffix}", current.text))
}

// 未提供／匂いなし／嗅げない状態は、それぞれの固定回答へ分ける。
// presentでは残った全成分を説明し、混合を「何だか分からない匂い」へ潰さない。
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
    speech_components(&o.components)
}
pub(super) fn speech_components(components: &[SmellComponent]) -> Speech {
    let mut components: Vec<_> = components.iter().collect();
    components.sort_by_key(|c| catalog::enum_text(&c.smell_id));
    // 強さ・混合を反映した本文なので、旧録音cueでは置き換えない。
    Speech::new(
        "smell",
        components
            .into_iter()
            .map(component_speech)
            .collect::<Vec<_>>()
            .join(""),
    )
}

fn cue(id: &'static str, text: String) -> Speech {
    let mut s = Speech::new("smell", text);
    s.cue_id = Some(id);
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    fn event(strength: i64) -> GameEvent {
        GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture","sequence":1,
            "observed_at":"2026-10-01T00:00:00Z","event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{},"world":{},"smell_observation":{"status":"present","components":[{"smell_id":"zombie","category":"decay","valence":"unpleasant","source_kind":"entity","effective_strength":strength}],"basis":"smell_policy_v2"}})).unwrap()
    }
    #[test]
    fn cancelled_strength_estimate_gets_one_stable_retry_then_normal_cooldown() {
        let settings = Settings::default();
        let mut presence = Presence::default();
        let first = event(5);
        presence.update(&first);
        presence.update(&first);
        assert!(presence.action(&first, 1000, &settings).is_some());
        let second = event(8);
        presence.reconsider_estimate(&second);
        assert!(presence.action(&second, 1100, &settings).is_none());
        presence.update(&second);
        assert!(presence.action(&second, 1200, &settings).is_some());
        let third = event(3);
        presence.reconsider_estimate(&third);
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
