//! 手持ち・所持品・周辺観測を、句に使う少数の表示名へ選び直す。
//! 道具以外の手持ちはそのまま主材料にし、道具を持つ場合は所持品の重みから一品を選ぶ。
//! ほかの所持品は分類の近い二品と異なる一品、周辺物は近距離の上限件数でまとめる。
use super::*;
/// 句の手もと材料一品。sourceはhand（実際の手持ち）かpocket（所持品からの選択）を区別する。
#[derive(Clone, Debug, Serialize)]
pub struct PoemItem {
    pub id: String,
    pub label: String,
    pub source: String,
}
/// namespaceを除いたIDの末尾で、別の所持品を句の主材料にできる道具類を判別する。
/// この分類は材料選択用で、持ち替え可能性やゲーム操作の許可を表さない。
pub fn is_work_tool(id: &str) -> bool {
    let id = strip(id.rsplit(':').next().unwrap_or("")).to_lowercase();
    !id.is_empty()
        && id != "air"
        && [
            "pickaxe",
            "shovel",
            "axe",
            "hoe",
            "sword",
            "bow",
            "crossbow",
            "trident",
            "mace",
            "spear",
            "shears",
            "fishing_rod",
            "brush",
            "shield",
        ]
        .iter()
        .any(|s| id.ends_with(s))
}
/// 道具を持つときの所持品候補へ重みを付ける。鉱物等12、花・染料11、装飾等10、食べ物9を使う。
/// よく集まる石土等は1に抑え、未分類品だけ6+所持数（最大4）にする。季節や俳句の季語の重みではない。
pub fn pocket_weight(id: &str, count: i64) -> i64 {
    let id = strip(id.rsplit(':').next().unwrap_or("")).to_lowercase();
    if [
        "dirt",
        "coarse_dirt",
        "cobblestone",
        "cobbled_deepslate",
        "stone",
        "netherrack",
        "gravel",
        "sand",
        "red_sand",
        "andesite",
        "diorite",
        "granite",
        "tuff",
        "deepslate",
        "stick",
        "arrow",
    ]
    .contains(&id.as_str())
    {
        return 1;
    }
    if [
        "_ore",
        "_ingot",
        "_nugget",
        "diamond",
        "emerald",
        "netherite",
    ]
    .iter()
    .any(|s| id.ends_with(s))
        || ["totem_of_undying", "elytra", "nether_star"].contains(&id.as_str())
    {
        return 12;
    }
    if [
        "flower",
        "tulip",
        "orchid",
        "lilac",
        "rose",
        "peony",
        "sunflower",
        "dandelion",
        "poppy",
        "allium",
        "azure",
        "cornflower",
        "lily",
        "torchflower",
        "pitcher",
        "spore_blossom",
        "dye",
    ]
    .iter()
    .any(|s| id.contains(s))
    {
        return 11;
    }
    if [
        "pressure_plate",
        "button",
        "lantern",
        "candle",
        "book",
        "map",
        "banner",
        "music_disc",
        "goat_horn",
        "pottery",
        "sherd",
        "smithing",
    ]
    .iter()
    .any(|s| id.contains(s))
    {
        return 10;
    }
    if [
        "apple", "bread", "stew", "soup", "berry", "melon", "potato", "carrot", "beef", "pork",
        "chicken", "mutton", "fish", "salmon", "cookie", "cake", "pie", "honey",
    ]
    .iter()
    .any(|s| id.contains(s))
    {
        return 9;
    }
    if ["_log", "_planks", "_sapling", "_leaves", "_wool", "_carpet"]
        .iter()
        .any(|s| id.ends_with(s))
    {
        return 4;
    }
    if id.contains("torch") || ["campfire", "soul_campfire"].contains(&id.as_str()) {
        return 5;
    }
    6 + count.clamp(0, 4)
}
/// 道具以外の手持ちはそのまま返し、道具なら非道具の所持品から主材料を一品選ぶ。
/// inventory_orderは受信JSONに現れた全キーを一度ずつ含む必要がある。同じ表示名の候補を最初の一品へまとめるため。
/// 候補なしなら実際の手持ちへ戻る。道具以外を持つ経路では所持品順の検査自体を省く。
pub fn poem_item(
    event: &GameEvent,
    world: &WorldCatalog,
    inventory_order: &[String],
) -> Result<PoemItem> {
    let held = event.player.held_item.as_deref().unwrap_or("");
    let fallback = PoemItem {
        id: normalize_id(held),
        label: world.item_label(Some(held)),
        source: "hand".into(),
    };
    if !is_work_tool(held) {
        return Ok(fallback);
    }
    let valid = inventory_order.len() == event.inventory.len()
        && inventory_order.iter().collect::<HashSet<_>>().len() == inventory_order.len()
        && inventory_order
            .iter()
            .all(|id| event.inventory.contains_key(id));
    anyhow::ensure!(
        valid,
        "inventory order must contain every observed key exactly once"
    );
    let mut scored = Vec::<(i64, String, String)>::new();
    let mut seen = HashSet::new();
    for id in inventory_order {
        let count = event.inventory[id];
        if count <= 0 || is_work_tool(id) {
            continue;
        }
        let nid = strip(id.rsplit(':').next().unwrap_or("")).to_lowercase();
        if nid.is_empty() || nid == "air" {
            continue;
        }
        let label = world.item_label(Some(id));
        if label.is_empty() || !seen.insert(label.clone()) {
            continue;
        }
        scored.push((pocket_weight(id, count), label, nid));
    }
    let Some(max) = scored.iter().map(|v| v.0).max() else {
        return Ok(fallback);
    };
    // 最上位から2点以内の品を残し、名前とIDで安定した順序へ揃える。
    // sequenceと名前から選択位置を作るので、同じ観測は再現でき、最大点の一品だけにも固定されない。
    scored.retain(|v| v.0 >= max - 2);
    scored.sort_by(|a, b| (&a.1, &a.2).cmp(&(&b.1, &b.2)));
    let name = event
        .player
        .name
        .as_deref()
        .filter(|v| !v.is_empty())
        .unwrap_or("p");
    let seed = i128::from(event.sequence.unwrap_or(0)) * 1009
        + name
            .chars()
            .take(12)
            .map(|c| i128::from(c as u32))
            .sum::<i128>();
    let selected = &scored[seed.rem_euclid(scored.len() as i128) as usize];
    Ok(PoemItem {
        id: selected.2.clone(),
        label: selected.1.clone(),
        source: "pocket".into(),
    })
}
/// 分類の近い二品・異なる一品と、それらを同じ順でまとめた表示用リスト。
#[derive(Clone, Debug, Serialize)]
pub struct InventorySelection {
    pub close_pair: Vec<String>,
    pub far_item: String,
    pub items: Vec<String>,
}
#[derive(Clone)]
struct Candidate {
    label: String,
    section: String,
    path: Vec<String>,
    count: i64,
    order: usize,
}
/// カタログ分類の共通接頭段数を3倍し、同じsectionなら4点を足す。名称の文字列類似度ではない。
fn similarity(a: &Candidate, b: &Candidate) -> usize {
    a.path
        .iter()
        .zip(&b.path)
        .take_while(|(a, b)| a == b)
        .count()
        * 3
        + if !a.section.is_empty() && a.section == b.section {
            4
        } else {
            0
        }
}
/// 実際の手持ちを除いた所持品から、分類の近い二品と、その二品から最も離れた一品を返す。
/// 二品の同点は合計所持数、元の候補順で決める。異なる一品の同点は所持数が多い方を優先する。
/// 二品未満ならある品だけ、二品しかなければfar_itemなしになる。
pub fn inventory_values(
    event: &GameEvent,
    world: &WorldCatalog,
    entries: &impl Entries,
) -> InventorySelection {
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
    let mut seen = HashSet::new();
    let mut candidates = Vec::new();
    for (order, (id, count)) in sorted_inventory(event).into_iter().enumerate() {
        if *count <= 0 || strip(id.rsplit(':').next().unwrap_or("")).to_lowercase() == held {
            continue;
        }
        let label = world.item_label(Some(id));
        if label.is_empty() || !seen.insert(label.clone()) {
            continue;
        }
        let entry = entries.item_entry(id).unwrap_or(&Value::Null);
        candidates.push(Candidate {
            label,
            section: field(entry, "section"),
            path: array_strings(&entry["group_path"]),
            count: *count,
            order,
        });
    }
    if candidates.len() < 2 {
        return InventorySelection {
            close_pair: vec![],
            far_item: String::new(),
            items: candidates.into_iter().map(|c| c.label).collect(),
        };
    }
    let mut best: Option<(usize, i128, i128)> = None;
    let mut pair = (0, 1);
    for left in 0..candidates.len() - 1 {
        for right in left + 1..candidates.len() {
            let (a, b) = (&candidates[left], &candidates[right]);
            let score = (
                similarity(a, b),
                i128::from(a.count) + i128::from(b.count),
                -(a.order.min(b.order) as i128),
            );
            if best.is_none_or(|prior| score > prior) {
                best = Some(score);
                pair = (left, right);
            }
        }
    }
    let close_pair = vec![
        candidates[pair.0].label.clone(),
        candidates[pair.1].label.clone(),
    ];
    let outlier = (0..candidates.len())
        .filter(|i| *i != pair.0 && *i != pair.1)
        .min_by_key(|i| {
            let c = &candidates[*i];
            (
                similarity(c, &candidates[pair.0]).max(similarity(c, &candidates[pair.1])),
                -i128::from(c.count),
                c.order,
            )
        });
    let far_item = outlier
        .map(|i| candidates[i].label.clone())
        .unwrap_or_default();
    let mut items = close_pair.clone();
    if !far_item.is_empty() {
        items.push(far_item.clone());
    }
    InventorySelection {
        close_pair,
        far_item,
        items,
    }
}
/// 距離順に表示名の重複を除いて最大6件を取り、その6件の中で自然ブロックを前へ並べる。
/// 遠い自然ブロックを探して候補枠へ割り込ませる処理ではない。距離不明は末尾へ送る。
pub fn nearby_blocks(
    event: &GameEvent,
    world: &WorldCatalog,
    entries: &impl Entries,
) -> Vec<String> {
    let mut resources: Vec<_> = event.nearby_resources.iter().collect();
    resources.sort_by(|a, b| distance_cmp(a.distance, b.distance));
    let mut natural = vec![];
    let mut other = vec![];
    let mut seen = HashSet::new();
    for r in resources {
        let label = world.block_label(Some(&r.name));
        if label.is_empty() || !seen.insert(label.clone()) {
            continue;
        }
        let entry = entries.block_entry(&r.name).unwrap_or(&Value::Null);
        if entry["section"] == "natural_blocks" {
            natural.push(label);
        } else {
            other.push(label);
        }
        if natural.len() + other.len() >= 6 {
            break;
        }
    }
    natural.extend(other);
    natural
}
/// 近い落下物を表示名で重複排除し、最大4件を「地面に〜が落ちている」という観測文へ変える。
/// 所持品と混同しないよう落下状態を残し、距離不明の候補は後ろへ置く。
pub fn dropped_items(event: &GameEvent, world: &WorldCatalog) -> Vec<String> {
    let mut dropped: Vec<_> = event.dropped_items.iter().collect();
    dropped.sort_by(|a, b| distance_cmp(a.distance, b.distance));
    let mut values = vec![];
    let mut seen = HashSet::new();
    for d in dropped {
        let label = world.item_label(Some(&d.name));
        if label.is_empty() || !seen.insert(label.clone()) {
            continue;
        }
        values.push(format!("地面に{label}が落ちている"));
        if values.len() >= 4 {
            break;
        }
    }
    values
}
