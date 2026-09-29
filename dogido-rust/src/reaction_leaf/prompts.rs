use crate::types::{ChatMessage, Role};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use std::sync::LazyLock;
static DATA: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("prompts.json")).expect("reaction prompt data")
});

pub(super) fn truth(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(v) => *v,
        Value::String(v) => !v.is_empty(),
        Value::Array(v) => !v.is_empty(),
        Value::Object(v) => !v.is_empty(),
        Value::Number(v) => v.as_f64() != Some(0.0),
    }
}
pub(super) fn pystr(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(v) => v.clone(),
        _ => v.to_string(),
    }
}
fn get(d: &Value, key: &str, default: &str) -> String {
    d.get(key).map(pystr).unwrap_or_else(|| default.into())
}
fn or(d: &Value, key: &str, default: &str) -> String {
    d.get(key)
        .filter(|v| truth(v))
        .map(pystr)
        .unwrap_or_else(|| default.into())
}
fn trimmed(d: &Value, key: &str, default: &str, empty: &str) -> String {
    let v = get(d, key, default);
    if v.trim().is_empty() {
        empty.into()
    } else {
        v.trim().into()
    }
}
fn joined(d: &Value, key: &str, sep: &str, limit: usize, empty: &str) -> String {
    let v = d[key]
        .as_array()
        .map(|v| {
            v.iter()
                .take(limit)
                .map(pystr)
                .collect::<Vec<_>>()
                .join(sep)
        })
        .unwrap_or_default();
    if v.is_empty() { empty.into() } else { v }
}
fn integer(d: &Value, key: &str, default: i64) -> Result<i64> {
    let Some(v) = d.get(key).filter(|v| truth(v)) else {
        return Ok(default);
    };
    match v {
        Value::Number(n) => Ok(n
            .as_i64()
            .unwrap_or_else(|| n.as_f64().unwrap_or(default as f64) as i64)),
        Value::Bool(v) => Ok(i64::from(*v)),
        Value::String(v) => v.trim().parse().context("reaction integer"),
        _ => bail!("reaction integer"),
    }
}
fn fields(kind: &str, d: &Value) -> Result<Value> {
    let mut f = json!({});
    f["hostiles"] = joined(
        d,
        "hostiles",
        "、",
        usize::MAX,
        if kind == "aftermath" {
            "敵"
        } else {
            "敵なし"
        },
    )
    .into();
    f["time_phase"] = get(d, "time_phase", "unknown").into();
    f["hostile"] = get(d, "hostile", "敵").into();
    f["death_hostile"] = or(d, "hostile", "なし").into();
    f["variation_hint"] = get(
        d,
        "variation_hint",
        if kind == "deep_dark_ominous_sound" {
            "嫌な予感"
        } else {
            "気配"
        },
    )
    .into();
    match kind {
        "aftermath" => {
            let clear = truth(&d["hostile_clear_confirmed"]);
            f["clear_fact"]=if clear {"コード側で現在の視認敵・敵音・周辺敵数がすべて空と確認済み。戦闘は終了し、現在の観測範囲に残っている敵は0体。\n"}else{"戦闘は一段落したが、残敵なしの確認までは取れていない。\n"}.into();
            f["clear_guard"] = if clear {
                "確認済みなので『まだいるかも』『ほんまにおらんのか』のように残敵を疑わない。"
            } else {
                "残敵を断定しない。"
            }
            .into();
            let (fact, guard) = match or(d, "combat_outcome", "disengaged").as_str() {
                "player_kill" => (
                    "追跡していた敵の死亡と、プレイヤーによる撃破根拠を確認済み。プレイヤーが敵を倒したこととして、短く喜んでよい。\n",
                    "『倒した』『倒せた』と言ってよい。",
                ),
                "charged_creeper_detonated" => (
                    "追跡していた帯電クリーパーが実際に爆発して消えたことを確認済み。強い爆発への驚きや慌てた気持ちを短く出してよい。\n",
                    "プレイヤーが倒したとは言わない。",
                ),
                "creeper_detonated" => (
                    "追跡していたクリーパーが実際に爆発して消えたことを確認済み。爆発への驚きや慌てた気持ちを短く出してよい。\n",
                    "プレイヤーが倒したとは言わない。",
                ),
                "explosion_death" => (
                    "追跡していた敵が爆発による致死ダメージで死亡したことを確認済み。\n",
                    "『爆発で敵が倒れた』とは言ってよいが、プレイヤーが倒したとは断定しない。",
                ),
                "hostile_defeated" => (
                    "追跡していた敵の死亡は確認済み。ただし、誰が倒したかは確認できていない。\n",
                    "『敵が倒れた』とは言ってよいが、プレイヤーが倒したとは断定しない。",
                ),
                _ => (
                    "敵の死亡は確認していない。現在は敵の気配が観測範囲から遠のいただけ。\n",
                    "『倒した』『倒せた』『退治した』『敵を排除した』とは言わない。距離が取れた、気配が遠のいた、一段落した、の範囲で話す。",
                ),
            };
            f["outcome_fact"] = fact.into();
            f["outcome_guard"] = guard.into();
        }
        "ambient" => {
            f["candidate_lines"] = joined(d, "fallback_candidates", " / ", 4, "なし").into();
            f["mob_tags"] = joined(d, "mob_tags", "、", 6, "なし").into();
            f["mob_role"] = trimmed(d, "mob_role", "", "なし").into();
            f["temperament"] = trimmed(d, "mob_temperament", "friendly", "friendly").into();
            f["caution_reason"] = trimmed(d, "mob_caution_reason", "", "なし").into();
            f["variation_hint"] = [
                "観察寄りで入る",
                "感想寄りで入る",
                "軽い注意から入る",
                "共感や愛嬌寄りで入る",
            ][integer(d, "variation_slot", 0)?.rem_euclid(4) as usize]
                .into();
            let schedule = or(d, "villager_schedule_ja", "").trim().to_owned();
            let profession = or(d, "mob_profession", "").trim().to_owned();
            let mut parts = vec![];
            if !schedule.is_empty() {
                parts.push(format!("活動={schedule}"));
            }
            if !profession.is_empty() {
                parts.push(format!("職={}", get(d, "mob", &profession)));
            } else if truth(&d["mob_is_baby"]) {
                parts.push("子供".into());
            }
            f["villager_bits"] = if parts.is_empty() {
                String::new()
            } else {
                format!("村人メモ: {}。観察に使ってよい。\n", parts.join(" / "))
            }
            .into();
        }
        "dark_push_after_breath" => {
            f["cave_afterthought"]=match f["time_phase"].as_str(){Some("evening")=>"洞窟から出たらもう夜で、安心しきれず『一難去ってまた一難』みたいな気分になっている。\n",Some("night")=>"洞窟から出てもまだ夜で、安心しきれず『一難去ってまた一難』みたいな気分になっている。\n",_=>""}.into();
        }
        "light_source_gain" => {
            f["reason_line"] =
                if or(d, "comment_action", "acknowledge_supply_gain") == "relief_after_darkness" {
                    "暗い場所で怯えていたが、現在観測では暗さが実際に改善したので、短く安堵する。\n"
                } else {
                    "備えが増えた小さな節目として、短く安心する。\n"
                }
                .into();
        }
        "weather_transition" => {
            f["scene"] = get(d, "scene", "weather_transition").into();
            f["cold_biome_note"] = if truth(&d["cold_biome"]) {
                "寒い地域なので、雨は雪っぽい感覚で受け取る。\n"
            } else {
                ""
            }
            .into();
            f["dry_biome_note"] = if truth(&d["dry_biome"]) {
                "乾燥帯なので、雨は降らず空が曇ったり雷が鳴るだけ。\n"
            } else {
                ""
            }
            .into();
            f["thunder_note"] = "".into();
            f["length_note"] = "会話っぽい一言を24〜42文字くらいで返す。".into();
            if truth(&d["thunder_reaction"]) {
                let strike = if truth(&d["nearby_lightning"]) {
                    "近くへの落雷を実測しているので『今落ちた』『近かった』程度は言ってよい。"
                } else {
                    "雷鳴を聞いただけなので、近くへ落雷したとは断定しない。"
                };
                f["thunder_note"]=format!("これは天候変化の説明ではなく、鳴り続ける雷への短い独り言。\ncue 側で驚き声は済んでいるので、悲鳴を文字で重ねず小さく怖がる。\n{strike}\n").into();
                f["length_note"] = "ぶつぶつ漏れる会話っぽい一言を18〜34文字くらいで返す。".into();
            }
        }
        "structure_entry" => {
            f["note"] = trimmed(d, "structure_note", "", "なし").into();
        }
        "ender_eye_throw" => {
            f["reference_text"] = joined(d, "reference_lines", " / ", 5, "なし").into();
        }
        "portal_appearance" => {
            f["portal_label"] = get(d, "portal_label", "ポータル").into();
            let (color, mood) = match get(d, "portal_type", "").as_str() {
                "nether_portal" => (
                    "紫色の光が渦巻いている",
                    "異世界への入口が開いた。暑そうで少し怖い",
                ),
                "end_portal" => (
                    "中に緑色の星空のような模様が見える",
                    "最終目的地への門が開いた。飛び込む覚悟が必要",
                ),
                "end_gateway" => (
                    "小さな紫色のビーム状のゲートが空中に浮いている",
                    "新しいワープポイントが出現した。狭いけどどこかへ飛ばされそう",
                ),
                _ => ("不思議な光を放っている", "異世界への入口が見える"),
            };
            f["color_hint"] = color.into();
            f["mood_hint"] = mood.into();
        }
        "deep_dark_ominous_sound" => {
            f["ominous_kind"] = get(d, "ominous_kind", "unknown").into();
            f["stage"] = integer(d, "ominous_stage", 1)?.to_string().into();
        }
        _ => {}
    }
    Ok(f)
}
fn mode(kind: &str, d: &Value) -> &'static str {
    let explicit = pystr(&d["character_mode"]).trim().to_lowercase();
    match explicit.as_str() {
        "peace" | "peaceful" | "calm" | "平和" => "peace",
        "battle" | "combat" | "panic" | "fight" | "バトル" => "battle",
        "tension" | "alert" | "caution" | "緊張" => "tension",
        "workshop" | "haiku_workshop" | "ワークショップ" | "共同編集者" => "workshop",
        _ => DATA["default_modes"][kind].as_str().unwrap_or("peace"),
    }
}
pub(super) fn messages(kind: &str, d: &Value) -> Result<Vec<ChatMessage>> {
    let parts = DATA["templates"][kind]
        .as_array()
        .context("unsupported reaction kind")?;
    let f = fields(kind, d)?;
    let mut user = String::new();
    for part in parts {
        if let Some(text) = part["text"].as_str() {
            user.push_str(text);
        } else if let Some(key) = part["detail"].as_str() {
            user.push_str(&pystr(d.get(key).unwrap_or(&part["default"])));
        } else {
            let key = part["field"].as_str().context("reaction prompt part")?;
            user.push_str(f[key].as_str().context("reaction prompt field")?);
        }
    }
    Ok(vec![
        ChatMessage {
            role: Role::System,
            content: DATA["systems"][mode(kind, d)]
                .as_str()
                .context("reaction mode")?
                .into(),
        },
        ChatMessage {
            role: Role::User,
            content: user,
        },
    ])
}
