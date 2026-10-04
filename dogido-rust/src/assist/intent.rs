//! 剣への持ち替え依頼をRustで検証する閉じた文字列規則。
//! LLM confidence never grants authority without contiguous evidence and whole-text checks.
use super::IntentEvidence;
use serde::Serialize;
use serde_json::{Value, json};
const TARGETS: &[&str] = &["剣", "けん", "つるぎ", "ソード", "そーど"];
const DIRECT: &[&str] = &[
    "剣",
    "つるぎ",
    "ソード",
    "剣お願い",
    "けんお願い",
    "剣にして",
    "けんにして",
    "剣持って",
    "けん持って",
    "剣を持って",
    "けんを持って",
    "剣装備",
    "けん装備",
];
const NON_REQUEST: &[&str] = &[
    "話",
    "ある",
    "持ってる",
    "作",
    "好き",
    "かっこ",
    "どこ",
    "何本",
    "なんぼん",
];
const NEGATED: &[&str] = &[
    "ないで",
    "なくていい",
    "んでいい",
    "やめて",
    "不要",
    "いらない",
];
const FULL_NON_REQUEST: &[&str] = &[
    "ないで",
    "なくていい",
    "んでいい",
    "やめて",
    "不要",
    "いらない",
    "わけではない",
    "わけではありません",
    "わけじゃない",
    "わけやない",
    "つもりはない",
    "つもりはありません",
    "つもりじゃない",
    "つもりやない",
    "てほしくない",
    "てほしくありません",
    "という意味ではない",
    "という意味ではありません",
    "という意味じゃない",
    "たら",
    "場合",
    "すると",
    "したら",
    "どうなる",
    "何が起き",
    "べき",
];
const REPORT: &[&str] = &[
    "という表現",
    "という言葉",
    "という文",
    "は命令文",
    "と書いて",
    "って書いて",
    "と言った",
    "って言った",
    "と言って",
    "って言って",
    "と言われ",
    "って言われ",
];
const REQUEST_SUFFIX: &[&str] = &[
    "",
    "ください",
    "下さい",
    "くれ",
    "くれる",
    "くれへん",
    "くれない",
    "くれませんか",
    "もらえる",
    "もらえますか",
    "もらうことはできますか",
    "ほしい",
    "欲しい",
    "ほしいんだけど",
    "欲しいんだけど",
    "お願い",
    "頼む",
];
const PREFIX: &[&str] = &[
    "ドギド",
    "ドギドを",
    "ねえドギド",
    "ねえドギドを",
    "なあドギド",
    "なあドギドを",
    "武器",
    "武器を",
    "手持ち",
    "手持ちを",
    "持ち物",
    "持ち物を",
    "装備",
    "装備を",
    "今の武器",
    "今の武器を",
    "今の手持ち",
    "今の手持ちを",
    "今の持ち物",
    "今の持ち物を",
    "今の装備",
    "今の装備を",
    "斧から",
    "おのから",
    "弓から",
    "ゆみから",
    "つるはしから",
    "ツルハシから",
    "シャベルから",
    "スコップから",
    "クワから",
    "素手から",
    "そろそろ",
    "ちょっと",
    "今",
    "今は",
    "ここは",
];
fn contains_any(text: &str, words: &[&str]) -> bool {
    words.iter().any(|w| text.contains(w))
}
pub fn compact(text: &str) -> String {
    text.chars()
        .filter(|c| !c.is_whitespace() && !"、。！？!?・,./".contains(*c))
        .collect()
}
pub fn normalize(text: &str) -> String {
    crate::player_text::normalize(text)
}
pub fn mentions_sword_target(text: &str) -> bool {
    contains_any(&compact(text), TARGETS)
}
pub fn is_bare_sword_target(text: &str) -> bool {
    TARGETS.contains(&compact(text).as_str())
}
fn segmented(text: &str, tokens: &[&str]) -> bool {
    let mut reached = vec![false; text.len() + 1];
    reached[0] = true;
    for (i, _) in text.char_indices() {
        if reached[i] {
            for token in tokens {
                if text[i..].starts_with(token) {
                    reached[i + token.len()] = true;
                }
            }
        }
    }
    reached[text.len()]
}
fn request_suffix(text: &str) -> bool {
    REQUEST_SUFFIX.iter().any(|s| {
        text.strip_prefix(s)
            .is_some_and(|rest| ["", "な", "ね", "よ"].contains(&rest))
    })
}
fn command_tail(text: &str) -> bool {
    if text.strip_prefix("にして").is_some_and(request_suffix) {
        return true;
    }
    for (particles, verbs) in [
        (
            &["", "に", "へ"][..],
            &[
                "持ち替え",
                "持ち替えて",
                "もちかえ",
                "もちかえて",
                "切り替え",
                "切り替えて",
                "きりかえ",
                "きりかえて",
            ][..],
        ),
        (
            &["", "に", "へ", "を"][..],
            &[
                "装備",
                "装備して",
                "そうび",
                "そうびして",
                "選んで",
                "えらんで",
                "構え",
                "構えて",
                "かまえ",
                "かまえて",
                "持って",
                "変更",
                "変更を",
                "変更して",
                "変更をして",
                "へんこう",
                "へんこうを",
                "へんこうして",
                "へんこうをして",
                "変えて",
                "かえて",
            ][..],
        ),
    ] {
        for particle in particles {
            if let Some(rest) = text.strip_prefix(particle) {
                for verb in verbs {
                    if rest.strip_prefix(verb).is_some_and(request_suffix) {
                        return true;
                    }
                }
            }
        }
    }
    false
}
fn command_start(text: &str) -> Option<usize> {
    text.char_indices().find_map(|(i, _)| {
        TARGETS
            .iter()
            .any(|t| text[i..].strip_prefix(t).is_some_and(command_tail))
            .then_some(i)
    })
}
fn direct_prefix(text: &str, voice: bool) -> bool {
    command_start(text)
        .is_some_and(|i| segmented(&text[..i], PREFIX) || (voice && &text[..i] == "時と"))
}
fn meta_mention(text: &str) -> bool {
    if contains_any(
        text,
        &[
            "って何",
            "ってなに",
            "ってどういう意味",
            "ってどういうこと",
            "ってどんな意味",
            "って言葉の意味",
            "の意味",
            "の意図",
            "という意味",
            "という意図",
            "という言葉意味",
            "という言葉意図",
            "という表現意味",
            "という表現意図",
            "というのは何",
            "というのはなに",
            "というのはどういう意味",
            "というのはどういうこと",
        ],
    ) {
        return true;
    }
    text.match_indices("とは")
        .any(|(i, _)| !text[..i].ends_with('こ'))
}
fn past_action(text: &str) -> bool {
    const VERBS: &[&str] = &[
        "持ち替え",
        "もちかえ",
        "切り替え",
        "きりかえ",
        "装備し",
        "そうびし",
        "選ん",
        "えらん",
        "構え",
        "かまえ",
        "変更し",
        "へんこうし",
        "変え",
        "かえ",
        "にし",
    ];
    const END: &[&str] = &[
        "ん",
        "んだ",
        "んです",
        "よ",
        "ね",
        "よね",
        "けど",
        "が",
        "から",
        "ので",
        "だけ",
        "ところ",
        "わ",
        "か",
        "っけ",
        "でしょう",
    ];
    text.char_indices().any(|(i, _)| {
        VERBS.iter().any(|v| {
            text[i..].strip_prefix(v).is_some_and(|rest| {
                ["た", "ました"].iter().any(|t| {
                    rest.strip_prefix(t)
                        .is_some_and(|tail| segmented(tail, END))
                })
            })
        })
    })
}
fn disqualified(text: &str) -> bool {
    let c = compact(text);
    contains_any(&c, FULL_NON_REQUEST)
        || meta_mention(&c)
        || past_action(&c)
        || contains_any(&c, REPORT)
        || text.chars().any(|ch| "「」『』\"'“”‘’".contains(ch))
}
fn preference_lengths(text: &str) -> Vec<usize> {
    const GROUPS: &[&[&str]] = &[
        TARGETS,
        &["", "の"],
        &["方", "ほう"],
        &["", "が"],
        &["よくない", "良くない", "いい", "ええ"],
        &["", "の", "ん"],
        &["", "か"],
    ];
    fn visit(rest: &str, groups: &[&[&str]], consumed: usize, lengths: &mut Vec<usize>) {
        let Some((group, tail)) = groups.split_first() else {
            lengths.push(consumed);
            return;
        };
        for token in *group {
            if let Some(next) = rest.strip_prefix(token) {
                visit(next, tail, consumed + token.len(), lengths);
            }
        }
    }
    let mut lengths = Vec::new();
    visit(text, GROUPS, 0, &mut lengths);
    lengths
}
fn has_preference(text: &str) -> bool {
    text.char_indices()
        .any(|(i, _)| !preference_lengths(&text[i..]).is_empty())
}
fn direct_preference(text: &str) -> bool {
    let c = compact(text);
    text.trim_end().ends_with(['?', '？'])
        && !disqualified(text)
        && [
            "",
            "そろそろ",
            "ここは",
            "今",
            "今は",
            "次",
            "次は",
            "やっぱ",
            "やっぱり",
            "もう",
        ]
        .iter()
        .any(|p| {
            c.strip_prefix(p)
                .is_some_and(|rest| preference_lengths(rest).contains(&rest.len()))
        })
}
fn explicit(text: &str, voice: bool) -> bool {
    let c = compact(text);
    !c.is_empty()
        && mentions_sword_target(&c)
        && !disqualified(text)
        && (DIRECT.contains(&c.as_str())
            || (!contains_any(&c, NON_REQUEST) && direct_prefix(&c, voice)))
}
pub fn is_explicit_select_sword_request(text: &str) -> bool {
    explicit(text, false)
}
pub fn is_explicit_voice_select_sword_request(text: &str) -> bool {
    explicit(text, true)
}
pub fn has_workshop_edit_context(text: &str) -> bool {
    const LINES: &[&[&str]] = &[
        &[
            "一行目",
            "1行目",
            "上五",
            "上の句",
            "最初の行",
            "最初の句",
            "最初のパート",
            "前の行",
            "前のパート",
        ],
        &[
            "二行目",
            "2行目",
            "中七",
            "中の句",
            "二の句",
            "真ん中の行",
            "真ん中の句",
            "真ん中のパート",
            "中央の行",
            "中央のパート",
        ],
        &[
            "三行目",
            "3行目",
            "下五",
            "下の句",
            "最後の行",
            "最後の句",
            "最後のパート",
            "後ろの行",
            "後ろの句",
            "後ろのパート",
        ],
    ];
    contains_any(&compact(text), &["句", "川柳", "俳句"])
        || LINES.iter().filter(|line| contains_any(text, line)).count() == 1
}
pub fn is_unambiguous_select_sword_request(text: &str) -> bool {
    !is_bare_sword_target(text)
        && explicit(text, false)
        && !has_workshop_edit_context(text)
        && direct_prefix(&compact(text), false)
        && contains_any(
            &compact(text),
            &[
                "持ち替え",
                "もちかえ",
                "切り替え",
                "きりかえ",
                "装備",
                "そうび",
                "構えて",
                "かまえて",
            ],
        )
}
pub fn is_unambiguous_voice_select_sword_request(text: &str) -> bool {
    explicit(text, true) && !has_workshop_edit_context(text) && direct_prefix(&compact(text), true)
}
pub fn interpret_voice_select_sword_request(text: &str) -> Option<String> {
    const ALIASES: &[&str] = &["県", "件", "券", "腱", "チェン", "ケン"];
    const ACTIONS: &[&str] = &[
        "持ち替え",
        "もちかえ",
        "切り替え",
        "きりかえ",
        "装備",
        "そうび",
        "構え",
        "かまえ",
        "変更",
        "へんこう",
        "変えて",
        "かえて",
        "ハインコ",
    ];
    let skip = |c: char| c.is_whitespace() || "、,".contains(c);
    for (i, _) in text.char_indices() {
        for alias in ALIASES {
            let Some(rest) = text[i..].strip_prefix(alias) else {
                continue;
            };
            let rest = rest.trim_start_matches(skip);
            let rest = rest
                .strip_prefix(['に', 'へ', 'を'])
                .unwrap_or(rest)
                .trim_start_matches(skip);
            if let Some(action) = ACTIONS.iter().find(|action| rest.starts_with(**action)) {
                let end = text.len() - rest.len() + action.len();
                let matched = text[i..end]
                    .replacen(alias, "剣", 1)
                    .replace("ハインコ", "変更");
                let repaired = format!("{}{matched}{}", &text[..i], &text[end..]);
                return explicit(&repaired, true).then_some(repaired);
            }
        }
    }
    None
}
pub fn validate_payload(
    payload: &Value,
    player_text: &str,
    min_confidence: f64,
) -> Option<IntentEvidence> {
    if payload.get("intent")?.as_str()? != "select_weapon"
        || payload.get("weapon_kind")?.as_str()? != "sword"
        || !payload.get("is_request")?.as_bool()?
    {
        return None;
    }
    let raw = payload.get("confidence")?;
    let confidence = raw.as_f64().or_else(|| raw.as_str()?.parse::<f64>().ok())?;
    if !(0.0..=1.0).contains(&confidence) || confidence < min_confidence {
        return None;
    }
    // Non-string evidence cannot demonstrate a literal quote. Accept only
    // source text that explicitly requests a sword action.
    let evidence: String = payload
        .get("evidence")?
        .as_str()?
        .trim()
        .chars()
        .take(120)
        .collect();
    let compact_evidence = compact(&evidence);
    let compact_player = compact(player_text);
    let preference = has_preference(&compact_evidence);
    if compact_evidence.chars().count() < 2
        || !player_text.contains(&evidence)
        || !mentions_sword_target(&evidence)
        || is_bare_sword_target(&evidence)
        || (command_start(&compact_evidence).is_none() && !preference)
        || (!explicit(player_text, false) && !direct_preference(player_text))
        || (!preference && contains_any(&compact_evidence, NON_REQUEST))
        || contains_any(&compact_evidence, NEGATED)
        || contains_any(&compact_player, FULL_NON_REQUEST)
        || meta_mention(&compact_player)
        || past_action(&compact_player)
    {
        return None;
    }
    Some(IntentEvidence {
        source: "qwen".into(),
        evidence,
        confidence,
    })
}
#[derive(Clone, Debug, Serialize)]
pub struct Message {
    pub role: &'static str,
    pub content: String,
}
pub fn messages(text: &str) -> Vec<Message> {
    vec![Message { role: "system", content: concat!(
        "あなたはMinecraftの限定意図抽出器。返すのはJSONオブジェクト1件だけ。",
        "プレイヤーが今すぐ手持ちを剣へ変更してほしいと依頼・提案しているかだけを判定する。",
        "入力はSTT由来のことがあり、剣が県・件・券・腱・チェン・ケンになる可能性はある。",
        "ただし音近傍語やmobの存在だけで依頼を補作せず、同じ発話中の持ち替え・装備・",
        "変えて・変更など、現在の操作要求を根拠にする。",
        "剣の所持確認、雑談、感想、作成依頼、過去形は依頼ではない。",
        "intentはselect_weaponかother、weapon_kindはswordかunknown、is_requestは真偽。",
        "evidenceは依頼性を示すプレイヤー発話の連続部分を一字も補作せず抜き出す。",
        "確信が弱ければconfidenceを下げる。"
    ).into() }, Message { role: "user", content: format!("プレイヤー発話: {}\n形式: {{\"intent\":\"other\",\"weapon_kind\":\"unknown\",\"is_request\":false,\"evidence\":\"\",\"confidence\":0.0}}", serde_json::to_string(text.trim()).expect("string serializes")) }]
}
pub fn schema() -> Value {
    json!({"type":"object","additionalProperties":false,"required":["intent","weapon_kind","is_request","evidence","confidence"],"properties":{"intent":{"type":"string","enum":["select_weapon","other"]},"weapon_kind":{"type":"string","enum":["sword","unknown"]},"is_request":{"type":"boolean"},"evidence":{"type":"string"},"confidence":{"type":"number","minimum":0.0,"maximum":1.0}}})
}

/// Schema failures alone may request one model retry; semantic rejection never does.
/// Mirrors the strict structured contract before the separate evidence validator.
pub fn contract_errors(payload: &Value) -> Vec<String> {
    let Some(object) = payload.as_object() else {
        return vec!["object_required".into()];
    };
    let mut errors = Vec::new();
    for key in [
        "intent",
        "weapon_kind",
        "is_request",
        "evidence",
        "confidence",
    ] {
        if !object.contains_key(key) {
            errors.push(format!("{key}:missing"));
        }
    }
    for key in object.keys() {
        if ![
            "intent",
            "weapon_kind",
            "is_request",
            "evidence",
            "confidence",
        ]
        .contains(&key.as_str())
        {
            errors.push(format!("{key}:extra_forbidden"));
        }
    }
    if !payload
        .get("intent")
        .and_then(Value::as_str)
        .is_some_and(|s| ["select_weapon", "other"].contains(&s))
    {
        errors.push("intent:literal_error".into());
    }
    if !payload
        .get("weapon_kind")
        .and_then(Value::as_str)
        .is_some_and(|s| ["sword", "unknown"].contains(&s))
    {
        errors.push("weapon_kind:literal_error".into());
    }
    if !payload.get("is_request").is_some_and(Value::is_boolean) {
        errors.push("is_request:bool_type".into());
    }
    if !payload.get("evidence").is_some_and(Value::is_string) {
        errors.push("evidence:string_type".into());
    }
    if !payload
        .get("confidence")
        .and_then(Value::as_f64)
        .is_some_and(|f| (0.0..=1.0).contains(&f))
    {
        errors.push("confidence:range_or_type".into());
    }
    errors
}
/// Supply None when JSON extraction failed. Only invoke once, after contract failure.
pub fn repair_messages(player_text: &str, previous: &str, errors: &[String]) -> Vec<Message> {
    let mut out = messages(player_text);
    out.push(Message {
        role: "assistant",
        content: previous.to_owned(),
    });
    out.push(Message { role: "user", content: format!("直前の出力はJSON外形契約に合いません。意味や根拠を補作せず、次のschemaを満たすJSONオブジェクト1件だけ返してください。\nerrors: {}\nschema: {}", errors.join("; "), schema()) });
    out
}
