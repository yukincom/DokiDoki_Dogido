use super::*;
/// Tool and reading constraints are hard. Player notes are separate soft values.
/// Call with the captured job event, selected scene, and a code-owned lesson snapshot.
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
    // Place readings are available with visible sky or a cave biome. This
    // material rule is separate from the environment observation projection.
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
