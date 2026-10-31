//! 見込み: 指示文を変えず固定部分を前へ移せば、モードや匂いが変わっても長い接頭部を共有できる。
//! 合格: 全分岐で旧本文の各行・出現数・byte数を保ち、共通接頭部が延びる。
//! 範囲: オフラインのプロンプト構築。実モデルの速度・生成品質は別途確認する。
use super::*;
use serde_json::json;
use std::collections::BTreeMap;

// 並べ替え前の順序を検証用に固定する。本文は変更対象外の共有資産を参照する。
fn previous_dialogue(mode_name: &str, purpose: DialoguePurpose, smell: bool) -> String {
    let key = match purpose {
        DialoguePurpose::Reply => "reply",
        DialoguePurpose::Ambient => "ambient",
    };
    [
        base(),
        mode(mode_name),
        grounding(),
        if smell {
            TEXT["observed_smell"].as_str().unwrap()
        } else {
            ""
        },
        if matches!(purpose, DialoguePurpose::Ambient) {
            ambient_request()
        } else {
            ""
        },
        TEXT["dialogue"][key].as_str().unwrap(),
        choice_contract(),
    ]
    .into_iter()
    .filter(|s| !s.is_empty())
    .collect::<Vec<_>>()
    .join("\n")
}

fn assert_same_text(before: &str, after: &str) {
    let lines = |s: &str| {
        let mut counts = BTreeMap::new();
        for line in s.lines() {
            *counts.entry(line.to_owned()).or_insert(0) += 1;
        }
        counts
    };
    assert_eq!(
        lines(before),
        lines(after),
        "指示の追加・削除・言い換え・重複がない"
    );
    assert_eq!(before.len(), after.len(), "空白を含む本文量を保つ");
}

fn common_chars(a: &str, b: &str) -> usize {
    a.chars().zip(b.chars()).take_while(|(a, b)| a == b).count()
}

// 見込み: 4モード・2目的・匂いの有無・身体説明の有無でも、同じ適用条件と全文を保てる。
// 合格: 48組で旧本文と行別出現数・byte数が一致し、JSON出力契約は1回だけ含む。
// 範囲: 共有system文字列のみ。生成結果の意味・速度は含まない。
#[test]
fn reordering_preserves_every_dialogue_section() {
    for mode in ["normal", "tension", "base", "workshop"] {
        for purpose in [DialoguePurpose::Reply, DialoguePurpose::Ambient] {
            for smell in [false, true] {
                for player in [None, Some("プレイヤー"), Some("はる")] {
                    let mut before = previous_dialogue(mode, purpose, smell);
                    if let Some(player) = player {
                        before.push('\n');
                        before.push_str(&body_observation(player));
                    }
                    let after = dialogue_with_body(mode, purpose, smell, player);
                    assert_same_text(&before, &after);
                    assert_eq!(after.matches(choice_contract()).count(), 1);
                }
            }
        }
    }
}

// 見込み: modeと匂いより前へ固定説明を寄せると、状態が変わっても説明部分まで共有できる。
// 合格: 通常→緊張＋匂いの変化で、身体説明末尾までが共通接頭部に入り、旧順序より長い。
// 範囲: 文字列の前方一致だけ。トークン数・KVキャッシュ・実測時間は含まない。
#[test]
fn fixed_guidance_remains_a_common_prefix_across_mode_and_smell_changes() {
    for purpose in [DialoguePurpose::Reply, DialoguePurpose::Ambient] {
        let a = dialogue_with_body("normal", purpose, false, Some("はる"));
        let b = dialogue_with_body("tension", purpose, true, Some("はる"));
        let body = body_observation("はる");
        let fixed_end = a.find(&body).unwrap() + body.len();
        assert!(b.starts_with(&a[..fixed_end]));
        assert!(
            common_chars(&a, &b)
                > common_chars(
                    &previous_dialogue("normal", purpose, false),
                    &previous_dialogue("tension", purpose, true)
                )
        );
        assert!(a.find(choice_contract()).unwrap() < a.find(&body).unwrap());
    }
}

// 見込み: 環境body契約は組立時に選び、固定説明の後ろへ当回の候補ID入り例を残せる。
// 合格: 8組で旧本文を保持し、契約は一つだけ。「下に示す」例は契約と身体説明の後ろ。
// 範囲: 実環境要求の構築と模擬観測・候補。実モデル・身体の実移動は含まない。
#[test]
fn environment_preserves_selected_contract_and_keeps_candidate_examples_last() {
    let options = json!({"available_actions":{"stay":"その場におる","inspect":"見物"},"candidates":[{"id":"example-candidate-7"}]});
    for smell in [false, true] {
        for actions in [false, true] {
            for body in [false, true] {
                let mut before = previous_dialogue("normal", DialoguePurpose::Ambient, smell);
                if actions {
                    before = before.strip_suffix(choice_contract()).unwrap().to_owned()
                        + TEXT["environment_body_contract"].as_str().unwrap();
                    before.push('\n');
                    before.push_str(&environment_body_examples(&options));
                }
                if body {
                    before.push('\n');
                    before.push_str(&body_observation("プレイヤー"));
                }
                let mut context = json!({"topic":"place","world_context":{"observations":{"smell":{"status":if smell {"present"} else {"none"}}}}});
                if body {
                    context["world_context"]["observations"]["body_observation"] =
                        json!({"status":"unavailable"});
                }
                if actions {
                    context["body_options"] = options.clone();
                }
                let prepared = crate::environment::reaction::Prepared::new(
                    &json!({
                        "kind":"environment_reaction", "model":"fixture", "max_tokens":2048,
                        "details":{"reaction_context":context},"fallback_text":"fixture"
                    }),
                    "fixture",
                )
                .unwrap();
                let after = &prepared.request.messages[0].content;
                assert_same_text(&before, after);
                assert_eq!(
                    after.matches(choice_contract()).count(),
                    usize::from(!actions)
                );
                assert_eq!(
                    after
                        .matches(TEXT["environment_body_contract"].as_str().unwrap())
                        .count(),
                    usize::from(actions)
                );
                if actions {
                    let example = environment_body_examples(&options);
                    assert!(after.ends_with(&example));
                    if body {
                        assert!(
                            after.find(&body_observation("プレイヤー")).unwrap()
                                < after.find(&example).unwrap()
                        );
                    }
                }
                assert_eq!(
                    serde_json::from_str::<Value>(&prepared.request.messages[1].content).unwrap(),
                    context
                );
            }
        }
    }
}

// 見込み: 実builderの出力を保存すれば、モデルを呼ばずに変更後全文をレビューできる。
// 合格: 合成入力27例のsystem/userを省略なしで保存し、各ファイルが実builderの出力と一致する。
// 範囲: 明示実行時だけ指定フォルダへ書き出す。ネットワーク・モデル・サービス起動は行わない。
#[test]
#[ignore = "DOGIDO_PROMPT_REVIEW_DIRを指定して全文を出力するオフライン確認用"]
fn export_full_review_prompts() {
    let folder = std::path::PathBuf::from(std::env::var_os("DOGIDO_PROMPT_REVIEW_DIR").unwrap());
    std::fs::create_dir_all(&folder).unwrap();
    let mut cases = Vec::new();
    let world = |smell: bool, body: bool| {
        let mut context = json!({"revision":1,"changes":[],"observations":{
            "observer":"dogido","dimension":"minecraft:overworld",
            "weather":{"label":"晴れ"},"time_phase":"day",
            "smell":if smell {json!({"status":"present","components":[{"label":"花の香り","effective_strength":3}]})} else {json!({"status":"none"})}
        }});
        if body {
            context["observations"]["body_observation"] = json!({"status":"observed","current":{
                "position_relation":{"position_status":"observed","facts":[
                    "ドギドは、プレイヤーの身体の向きを基準に、右側にいる。",
                    "ドギドとプレイヤーの足元間の直線距離は3ブロック。"
                ]},"vision":{"fact":"ドギドからプレイヤーの姿が見える。"}
            }});
        }
        context
    };
    for mode in ["normal", "tension", "base", "workshop"] {
        for smell in [false, true] {
            for body in [false, true] {
                let details = json!({"dialogue_choice":true,"character_mode":mode,
                    "player_name":"テストプレイヤー","user_text":"今どこにおる？",
                    "conversation_history":"user: 一緒に歩こう。\nassistant: ええで、一緒に行こか。",
                    "event_digest":"- 草原を一緒に歩いている（比較用の合成記録）",
                    "world_context":world(smell, body)});
                let messages = crate::chat_prompt::messages(&details).unwrap();
                cases.push(json!({"name":format!("chat-{mode}-smell{}-body{}",u8::from(smell),u8::from(body)),"details":details,"messages":messages}));
            }
        }
    }
    for smell in [false, true] {
        for actions in [false, true] {
            for body in [false, true] {
                let mut context = json!({"topic":"place","world_context":world(smell, body),
                    "recent":{"conversation":"user: 一緒に歩こう。\nassistant: ええで、一緒に行こか。"}});
                if actions {
                    context["body_options"] = json!({
                        "available_actions":{"stay":"その場におる","owner_face":"顔が見える位置へ近づく","inspect":"見物"},
                        "candidates":[{"id":"review-candidate-1","label":"黄色い花（比較用の合成候補）"}]
                    });
                }
                let prepared = crate::environment::reaction::Prepared::new(
                    &json!({
                        "kind":"environment_reaction", "model":"fixture", "max_tokens":2048,
                        "details":{"reaction_context":context},"fallback_text":"fixture"
                    }),
                    "fixture",
                )
                .unwrap();
                cases.push(json!({"name":format!("environment-smell{}-actions{}-body{}",u8::from(smell),u8::from(actions),u8::from(body)),"messages":prepared.request.messages}));
            }
        }
    }
    let fixture: Value =
        serde_json::from_str(include_str!("../reaction_leaf/fixtures.json")).unwrap();
    for kind in ["body_recall", "thunder_reaction", "occluded_entry_no_light"] {
        let details = if kind == "body_recall" {
            json!({"player_input":"ドギド、こっちにおいで"})
        } else {
            let row = fixture["prompts"]
                .as_array()
                .unwrap()
                .iter()
                .find(|r| r[0] == kind)
                .unwrap();
            fixture["pool"][row[1].as_u64().unwrap() as usize].clone()
        };
        let leaf = crate::reaction_leaf::Leaf::prepare(
            &json!({"kind":kind,"model":"fixture","details":details,
            "max_tokens":2048,"temperature":0.65,"fallback_text":"fixture"}),
            "fixture",
            2048,
        )
        .unwrap();
        cases.push(json!({"name":format!("reaction-{kind}"),"messages":leaf.request.messages}));
    }
    assert_eq!(cases.len(), 27);
    let mut combined =
        String::from("変更後のLLMプロンプト全文（モデル未実行・比較用の合成入力）\n\n");
    for case in &cases {
        let name = case["name"].as_str().unwrap();
        let mut text = format!("{name}\n\n");
        for message in case["messages"].as_array().unwrap() {
            text.push_str(&format!(
                "[{}]\n{}\n\n",
                message["role"].as_str().unwrap().to_uppercase(),
                message["content"].as_str().unwrap()
            ));
        }
        text.push_str("[END OF PROMPT]\n");
        std::fs::write(folder.join(format!("{name}.txt")), &text).unwrap();
        combined.push_str(&text);
    }
    std::fs::write(folder.join("all-prompts.txt"), combined).unwrap();
    std::fs::write(
        folder.join("requests.json"),
        serde_json::to_string_pretty(&json!({"synthetic":true,"model_called":false,"cases":cases}))
            .unwrap()
            + "\n",
    )
    .unwrap();
}
