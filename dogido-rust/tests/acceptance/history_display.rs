//! パート3: 実際の履歴表示と通常／環境の生成要求を通し、時刻・契機・返答先、UUID除外、今回の用件の区別を検査する。保持量やモデルの返答品質は表示改善で代用しない。
use super::history::History;
use crate::types::{ChatMessage, Role};
use serde_json::{Value, json};

const INPUT_ID: &str = "63c9853b-d8ba-4f2c-804d-30a54ef54023";
const AMBIENT_ID: &str = "b4956270-d336-43eb-9514-09f9df231680";
const WARNING_ID: &str = "3da06415-9b18-4660-9a0c-2352212ca19d";
const OLD_QUESTION: &str = "ドギドどこ？";
const CURRENT_QUESTION: &str = "さっき朝の話をしたのを覚えてる？";

fn rows() -> Vec<Value> {
    vec![
        json!({"turn_id":INPUT_ID,"role":"user","text":OLD_QUESTION,
            "occurred_at":"2026-10-28T00:00:00Z","utterance_kind":"player_input","reply_to_turn_id":null}),
        json!({"turn_id":format!("{INPUT_ID}:reply"),"role":"assistant","text":"右前やで。",
            "occurred_at":"2026-10-28T00:00:02Z","utterance_kind":"reply","reply_to_turn_id":INPUT_ID}),
        json!({"turn_id":format!("{AMBIENT_ID}:reply"),"role":"assistant","text":"朝になったな。",
            "occurred_at":"2026-10-28T00:00:10Z","utterance_kind":"spontaneous","reply_to_turn_id":null}),
        json!({"turn_id":format!("{WARNING_ID}:reply"),"role":"assistant","text":"朝になったな。",
            "occurred_at":"2026-10-28T00:00:12Z","utterance_kind":"warning","reply_to_turn_id":null}),
    ]
}

fn normal(rows: &[Value], current: &str) -> Vec<ChatMessage> {
    crate::chat_prompt::messages(&json!({
        "dialogue_choice":true,"user_text":current,
        "conversation_history":History::lines_for(rows),
        "world_context":{"observations":{"place":"WORLD_CONTEXT_SENTINEL","time_phase":"morning"}}
    }))
    .unwrap()
}

fn environment(rows: &[Value]) -> Vec<ChatMessage> {
    crate::environment::reaction::Prepared::new(&json!({
        "kind":"environment_reaction","model":"acceptance-fixture","max_tokens":256,
        "fallback_text":"周りを見てるで。",
        "details":{"reaction_context":{"topic":"surroundings",
            "recent":{"conversation":rows},
            "world_context":{"observations":{"place":"WORLD_CONTEXT_SENTINEL","time_phase":"morning"}}
        }}
    }), "acceptance-fixture").unwrap().request.messages
}

// JSONを使うbuilderでは表示文字列の改行をデコードするだけ。時刻・分類・返答先は作らない。
fn text_values(value: &Value, output: &mut Vec<String>) {
    match value {
        Value::String(text) => output.push(text.clone()),
        Value::Array(rows) => rows.iter().for_each(|v| text_values(v, output)),
        Value::Object(object) => object.values().for_each(|v| text_values(v, output)),
        _ => {}
    }
}

fn prompt_text(messages: &[ChatMessage]) -> String {
    let mut values = Vec::new();
    for message in messages {
        if let Ok(value) = serde_json::from_str::<Value>(&message.content) {
            text_values(&value, &mut values);
        } else {
            values.push(message.content.clone());
        }
    }
    values.join("\n")
}

// 同じ本文の自発発話と警告を含む履歴。各行に時刻・契機を示し、返答には元の質問を添え、保存用IDを生成要求へ出さない。
#[test]
fn acceptance_history_display_normal_identifies_time_kind_and_reply_target_per_row() {
    let rows = rows();
    let lines = History::lines_for(&rows);
    let reply = lines
        .lines()
        .find(|line| line.contains("右前やで。"))
        .unwrap();
    assert!(
        reply.contains("00:00:02") || reply.contains("09:00:02"),
        "返答行に確定時刻を表示する（fixtureはUTC 00:00:02 / JST 09:00:02）: {reply}"
    );
    assert!(reply.contains("返答"), "発話の契機を区別する: {reply}");
    assert!(
        reply.contains(OLD_QUESTION),
        "返答先の原文を同じ行に添える: {reply}"
    );
    let repeated: Vec<_> = lines
        .lines()
        .filter(|line| line.contains("朝になったな。"))
        .collect();
    assert_eq!(repeated.len(), 2, "同じ本文の別発話を間引かない");
    assert!(repeated[0].contains("自発発話"));
    assert!(repeated[1].contains("警告"));
    let actual = prompt_text(&normal(&rows, CURRENT_QUESTION));
    assert!(
        actual.contains(&lines),
        "実際の通常会話builderへ共通表示が届く"
    );
    for id in [INPUT_ID, AMBIENT_ID, WARNING_ID] {
        assert!(!actual.contains(id));
    }
}

// 同じ履歴を通常と環境の生成要求へ渡す。両者が共通表示を用い、環境側にもUUIDが残らず、同文の別発話を間引かない。
#[test]
fn acceptance_history_display_environment_uses_same_readable_history_without_uuid() {
    let rows = rows();
    let common = History::lines_for(&rows);
    let normal = prompt_text(&normal(&rows, CURRENT_QUESTION));
    let ambient = prompt_text(&environment(&rows));
    assert!(normal.contains(&common));
    for id in [INPUT_ID, AMBIENT_ID, WARNING_ID] {
        assert!(
            !ambient.contains(id),
            "環境反応のモデル入力へ保存用UUIDを露出しない: {id}"
        );
    }
    assert!(
        ambient.contains(&common),
        "通常会話と環境反応で同じ履歴表示を使用する"
    );
    assert_eq!(
        ambient.matches("朝になったな。").count(),
        2,
        "保持量を変えず両記録を渡す"
    );
}

// 自発発話が多い履歴と観測資料の後に今回の質問が来る場面。質問を一度だけ独立して末尾側へ置き、古い資料は減らさない。
#[test]
fn acceptance_history_display_current_question_follows_history_and_surrounding_material() {
    let mut rows = rows();
    for n in 0..46 {
        rows.push(
            json!({"turn_id":format!("spontaneous-{n}:reply"),"role":"assistant",
            "text":format!("周辺の古い話題{n}。"),"occurred_at":"2026-10-28T00:00:13Z",
            "utterance_kind":"spontaneous","reply_to_turn_id":null}),
        );
    }
    let messages = normal(&rows, CURRENT_QUESTION);
    let last = messages
        .last()
        .expect("今回の用件を最後のuser messageに置く");
    assert_eq!(last.role, Role::User);
    let text = prompt_text(&messages);
    assert_eq!(
        text.matches(CURRENT_QUESTION).count(),
        1,
        "今回の問いを重複注入しない"
    );
    let query = text.find(CURRENT_QUESTION).unwrap();
    assert!(query > text.rfind("周辺の古い話題45。").unwrap());
    assert!(
        query > text.rfind("WORLD_CONTEXT_SENTINEL").unwrap(),
        "今回の問いを大量の観測資料の前に埋めず、資料の後の独立した用件に置く"
    );
    assert!(last.content.contains(CURRENT_QUESTION));
    assert_eq!(
        text.matches("周辺の古い話題").count(),
        46,
        "資料を削って配置問題を隠さない"
    );
}

// 環境変化だけで判断する場面。過去の質問は履歴に残し、今回の新しいプレイヤー発言がないことを生成要求に明示する。
#[test]
fn acceptance_history_display_environment_declares_no_new_player_input() {
    let messages = environment(&rows());
    let text = prompt_text(&messages);
    assert!(
        text.contains("新しいプレイヤー発言はない") || text.contains("新しいプレイヤー入力はない"),
        "環境反応の今回の用件には新しい本人入力が無いことを明記する"
    );
    assert!(text.contains(OLD_QUESTION), "以前の質問は履歴として残す");
    assert!(
        !text.contains(CURRENT_QUESTION),
        "存在しない今回入力を作らない"
    );
}

// 旧形式の履歴には時刻と契機がない。本文やIDから警告と推測せず、両方が不明であることを表示する。
#[test]
fn acceptance_history_display_missing_metadata_stays_unknown() {
    let legacy =
        json!({"turn_id":"warning:legacy:reply","role":"assistant","text":"足元気ぃつけてな。"});
    let lines = History::lines_for(&[legacy]);
    assert!(
        lines.contains("時刻不明"),
        "旧行に表示時点の時刻を割り当てない"
    );
    assert!(
        lines.contains("契機不明"),
        "IDや警告らしい本文から発話契機を創作しない"
    );
}

// 返答先の入力が保持窓にない場面。保存用UUIDを表示せず、今回の問いへ付け替えず、返答対象不明を明示する。
#[test]
fn acceptance_history_display_missing_reply_target_stays_unknown() {
    let orphan = json!({"turn_id":"orphan:reply","role":"assistant","text":"右前やで。",
        "occurred_at":"2026-10-28T00:00:02Z","utterance_kind":"reply","reply_to_turn_id":INPUT_ID});
    let text = prompt_text(&normal(&[orphan], CURRENT_QUESTION));
    assert!(
        !text.contains(INPUT_ID),
        "返答先が窓の外でもUUIDを本文へ出さない"
    );
    assert!(
        text.contains("返答対象不明") || text.contains("返答先不明"),
        "対象のない過去の返答を今回の問いへ付け替えない"
    );
}

// 20往復と補助枠40件がある場面。保持量を縮めず全件を両生成要求へ届ける。本人の本文は返答対象としての再掲を許し、発話本文は重複させない。
#[test]
fn acceptance_history_display_control_keeps_all_retained_rows_in_both_requests() {
    let mut history = History::default();
    for n in 0..20 {
        let turn = format!("turn-{n}");
        history.push(&turn, "user", &format!("保存質問[{n}]"));
        history.push(&turn, "assistant", &format!("保存返答[{n}]"));
    }
    for n in 0..50 {
        history.push(
            &format!("ambient-{n}"),
            "assistant",
            &format!("保存自発[{n}]"),
        );
    }
    let rows = history.rows();
    assert_eq!(rows.len(), 80);
    assert_eq!(rows.iter().filter(|r| r["role"] == "user").count(), 20);
    assert!(!rows.iter().any(|r| r["text"] == "保存自発[9]"));
    assert!(rows.iter().any(|r| r["text"] == "保存自発[10]"));
    for messages in [normal(&rows, CURRENT_QUESTION), environment(&rows)] {
        let prompt = prompt_text(&messages);
        for row in &rows {
            let occurrences = prompt.matches(row["text"].as_str().unwrap()).count();
            assert!(occurrences > 0, "保持した本文を落とさない: {}", row["text"]);
            if row["role"] == "assistant" {
                assert_eq!(occurrences, 1, "発話本文を重複させない: {}", row["text"]);
            }
        }
    }
}
