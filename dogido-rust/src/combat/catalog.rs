use serde_json::Value;
use std::sync::LazyLock;
static COMBAT: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../data/responses/ques/combat.json"))
        .expect("combat catalog")
});
static BOSS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../data/responses/ques/boss.json"))
        .expect("boss catalog")
});
static THREATS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../threat_catalog.json")).expect("threat catalog")
});
pub fn labels() -> &'static Value {
    &THREATS["labels"]
}
pub fn node(topic: &str, keys: &[&str]) -> &'static Value {
    let mut value = match topic {
        "combat" => &*COMBAT,
        "boss" => &*BOSS,
        _ => panic!("unknown combat topic"),
    };
    for key in keys {
        value = &value[*key];
    }
    value
}
pub fn text(topic: &str, keys: &[&str]) -> String {
    node(topic, keys).as_str().expect("catalog text").to_owned()
}
