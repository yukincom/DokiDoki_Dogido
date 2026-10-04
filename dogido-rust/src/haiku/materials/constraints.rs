//! 発句時の道具・場面語・読み訂正と、有効なlessonから生成用の制約欄を作る。
//! 道具の語彙と既知の誤読はallowed/forbiddenへ、本人の好みは別のplayer_lessonsへ置く。
//! 返すのはsnapshotのJSONだけで、語句の採否やlessonの期限・保存はそれぞれの担当へ渡す。
use super::*;
/// 手持ちIDとsceneのmotifに一致する道具語彙を集め、使える場面ではバイオームの読みを加える。
/// lessonはloosen以外のnoteを重複なく最大3件の参考欄へ置き、forbidden_termsへ混ぜない。
/// 全欄が空ならNoneを返す。入力lessonの期限や抑止は呼出側で解決済みという前提を使う。
pub fn constraint_details(
    event: &GameEvent,
    scene: &Scene,
    world: &WorldCatalog,
    readings: &ReadingSnapshot,
    lessons: &[Value],
) -> Option<Value> {
    let families = RULES["noun_families"].as_array().expect("noun families");
    let held = strip(
        event
            .player
            .held_item
            .as_deref()
            .unwrap_or("")
            .rsplit(':')
            .next()
            .unwrap_or(""),
    )
    .to_lowercase();
    let mut selected = vec![];
    let mut keys = HashSet::new();
    for family in families {
        if array_strings(&family["item_suffixes"])
            .iter()
            .any(|s| held.ends_with(s))
            && keys.insert(field(family, "key"))
        {
            selected.push(family);
        }
    }
    // 手持ち由来の分類を先に残し、見どころで言及された道具分類を補う。
    // 複数分類が当たる場合もfamilyのkeyごとに一度だけ集め、語彙の重複は後で除く。
    for motif in &scene.motifs {
        for family in families {
            if !keys.contains(&field(family, "key"))
                && array_strings(&family["label_markers"])
                    .iter()
                    .any(|s| !s.is_empty() && motif.contains(s))
            {
                keys.insert(field(family, "key"));
                selected.push(family);
            }
        }
    }
    let mut allowed = unique(
        selected
            .iter()
            .flat_map(|f| array_strings(&f["allowed_terms"])),
    );
    let mut forbidden = unique(
        selected
            .iter()
            .flat_map(|f| array_strings(&f["forbidden_terms"])),
    );
    // 地名の読みは空が見えるときか洞窟バイオームで加える。地下でも洞窟そのものの名は使えるため。
    // これは読み制約を載せる条件で、見どころへ空や景色を載せる環境投影の判定とは分ける。
    let biome = biome_id(event.world.biome.as_deref());
    let visible =
        event.world.sky_visible == Some(true) || biome == "deep_dark" || biome.ends_with("_caves");
    if visible {
        let label = world.biome_label(event.world.biome.as_deref());
        let entry = world
            .biome_entry(event.world.biome.as_deref())
            .unwrap_or(&Value::Null);
        let raw_label = [field(entry, "label"), field(entry, "japanese")]
            .into_iter()
            .find(|s| !s.is_empty())
            .unwrap_or_default();
        let catalog = readings.resolve(
            strip(&raw_label),
            entry.get("reading").and_then(Value::as_str),
        );
        let reading = readings.resolve(strip(&label), catalog.as_deref());
        if let Some(ref r) = reading
            && !allowed.contains(r)
        {
            allowed.push(r.clone());
        }
        // 現在の正しい読みと同じ誤読記録は除き、許容語を同時に禁止することを避ける。
        for bad in readings.forbidden(strip(&label)) {
            if Some(&bad) != reading.as_ref() && !forbidden.contains(&bad) {
                forbidden.push(bad);
            }
        }
    }
    let mut notes = vec![];
    for row in lessons {
        if !row.is_object() {
            continue;
        }
        let polarity = field(row, "polarity");
        let polarity = if polarity.is_empty() {
            "tighten"
        } else {
            strip(&polarity)
        };
        if polarity.to_lowercase() == "loosen" {
            continue;
        }
        let raw = field(row, "note");
        let note = strip(&raw);
        if !note.is_empty() && !notes.iter().any(|v| v == note) {
            notes.push(note.to_owned());
        }
    }
    if allowed.is_empty() && forbidden.is_empty() && notes.is_empty() {
        return None;
    }
    let mut result = json!({"allowed_terms":allowed,"forbidden_terms":forbidden});
    if !notes.is_empty() {
        notes.truncate(3);
        result["player_lessons"] = json!(notes);
    }
    Some(result)
}
