use super::*;

fn frame(extra: Value) -> GameEvent {
    let mut value = json!({"schema_version":"2026-05-24","adapter":"fixture","sequence":1,
        "observed_at":"2026-10-03T00:00:00Z","event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld"},"world":{}});
    value
        .as_object_mut()
        .unwrap()
        .extend(extra.as_object().unwrap().clone());
    GameEvent::parse(value).unwrap()
}

#[test]
fn authored_portal_and_sculk_notes_survive_dictionary_storage_categories() {
    let event = frame(
        json!({"world":{"nearby_portal_type":"end_portal","ominous_sound_kind":"sculk_shrieker"}}),
    );
    let knowledge = for_event(&event, None);
    let portal = knowledge
        .entries
        .iter()
        .find(|e| e.id == "end_portal")
        .unwrap();
    let shrieker = knowledge
        .entries
        .iter()
        .find(|e| e.id == "sculk_shrieker")
        .unwrap();
    assert_eq!(
        portal.general["note"],
        "ワープゲート本体。中に緑色の星が煌めく"
    );
    assert_eq!(
        shrieker.general["note"],
        "角のような飾りがついている。スカルクセンサーが反応を受信し、叫び声をあげ、ウォーデンを喚ぶ。"
    );
    assert_eq!(shrieker.basis, ["heard"]);
    assert!(
        !knowledge
            .entries
            .iter()
            .any(|e| e.id == "warden" || e.id == "sculk_sensor")
    );
    assert_eq!(knowledge.entries.len(), 2);
}

#[test]
fn every_mob_keeps_all_authored_poetic_fields_without_eight_tag_clipping() {
    for (key, original) in chat_catalog::catalog().all_mob_entries() {
        let material = mob(key, None, false, "visual").unwrap();
        assert_eq!(material.general["poetic"], original["poetic"], "{key}");
    }
    let baby = mob("villager", None, true, "passive_observation").unwrap();
    let source = &entry_catalog::PASSIVE["items"]["villager"]["baby"];
    assert_eq!(baby.label, source["label"].as_str().unwrap());
    for (key, value) in source["poetic"].as_object().unwrap() {
        assert_eq!(baby.general["poetic"][key], *value);
    }
}

#[test]
fn event_knowledge_keeps_origin_and_does_not_lookup_unobserved_related_entities() {
    let event = frame(json!({"world":{"biome":"the_end"},
        "passive_mobs":[{"type":"cow"},{"type":"minecraft:cow"}],
        "auditory_threats":[{"label":"zombie","spoken_name_allowed":true},{"label":"creeper","spoken_name_allowed":false}],
        "look_target":{"kind":"block","name":"mod:sculk_shrieker"}}));
    let knowledge = for_event(&event, None);
    assert_eq!(
        knowledge.entries.iter().filter(|e| e.id == "cow").count(),
        1
    );
    assert_eq!(
        knowledge
            .entries
            .iter()
            .find(|e| e.id == "zombie")
            .unwrap()
            .basis,
        ["heard"]
    );
    assert!(
        !knowledge
            .entries
            .iter()
            .any(|e| e.id == "creeper" || e.id == "sculk_shrieker" || e.id == "end_gateway")
    );
    let biome = knowledge
        .entries
        .iter()
        .find(|e| e.kind == "biome")
        .unwrap();
    assert_eq!(
        biome.general["environment_notes"],
        entry_catalog::BIOMES["groups"]["end"]["biomes"]["the_end"]["environment_notes"]
    );
    assert_eq!(
        biome.general["group"]["description"]["overview"],
        entry_catalog::BIOMES["groups"]["end"]["overview"]
    );
    assert!(biome.general.get("temperature").is_none());
}

#[test]
fn selected_sound_and_former_enemy_are_available_without_claiming_current_sight() {
    let heard = for_reaction(
        "deep_dark_ominous_sound",
        &json!({"ominous_kind":"warden_heartbeat"}),
    );
    assert_eq!(heard.entries[0].id, "warden");
    assert_eq!(heard.entries[0].basis, ["heard"]);
    assert_eq!(
        heard.entries[0].general["poetic"]["role"],
        "魂を食らう冥府の王"
    );
    let former = for_reaction("aftermath", &json!({"hostiles":["ゾンビ"]}));
    assert_eq!(former.entries[0].basis, ["reaction_target"]);
    for (kind, details) in [
        ("death", json!({"hostile":"ゾンビ"})),
        ("darkness_escape", json!({"hostiles":["ゾンビ"]})),
        ("dark_push_no_light", json!({"hostiles":["ゾンビ"]})),
        ("dark_push_after_breath", json!({"hostiles":["ゾンビ"]})),
    ] {
        assert_eq!(for_reaction(kind, &details), former);
    }

    assert!(for_event(&frame(json!({})), None).is_empty());
    assert!(
        for_reaction(
            "deep_dark_ominous_sound",
            &json!({"ominous_kind":"unknown"})
        )
        .is_empty()
    );
}

#[test]
fn heard_animals_and_selected_villager_variants_keep_their_authored_materials() {
    let event = frame(json!({"ambient_sounds":[{"type":"minecraft:cow"},{"type":"wind"}]}));
    let heard = for_event(&event, None);
    assert_eq!(heard.entries.len(), 1);
    assert_eq!(heard.entries[0].id, "cow");
    assert_eq!(heard.entries[0].basis, ["heard"]);
    for (label, profession, baby) in [("子供", "none", true), ("農民", "farmer", false)] {
        let selected = for_reaction(
            "ambient",
            &json!({"mob":label,"mob_profession":profession,"mob_is_baby":baby,
            "__ambient_guard":{"mob_type":"villager"}}),
        );
        let expected = mob("villager", Some(profession), baby, "reaction_target").unwrap();
        assert_eq!(selected.entries, [expected]);
    }
    let event = frame(
        json!({"look_target":{"kind":"entity","name":"villager","identity":{"entity_id":"child"}},
        "passive_mobs":[{"type":"villager","identity":{"entity_id":"child"},"is_baby":true}]}),
    );
    let knowledge = for_event(&event, None);
    assert_eq!(knowledge.entries.len(), 1);
    assert_eq!(knowledge.entries[0].label, "子供");
    assert_eq!(
        knowledge.entries[0].basis,
        ["look_target", "passive_observation"]
    );
}
