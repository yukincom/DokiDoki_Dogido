use super::*;
fn atoms(value: &Value) -> Vec<SourceAtom> {
    serde_json::from_value(value.clone()).unwrap()
}
fn sources(value: &Value) -> Vec<CatalogSourceSnapshot> {
    serde_json::from_value(value.clone()).unwrap()
}
#[test]
fn canonical_source_atom_and_stored_reader_goldens() {
    let rows: Vec<Value> = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    assert!(rows.len() > 1800);
    for (i, row) in rows.iter().enumerate() {
        let input = &row["input"];
        let actual = match row["op"].as_str().unwrap() {
            "snapshot" => {
                let source = catalog_source_snapshot(
                    input["catalog_type"].as_str().unwrap(),
                    input["catalog_id"].as_str().unwrap(),
                    &input["entry"],
                    input["observation_role"].as_str().unwrap(),
                    input["fallback_label"].as_str().unwrap(),
                );
                json!({"snapshot":source,"projection":source.as_ref().map(CatalogSourceSnapshot::to_dict)})
            }
            "split" => json!(split_note_sentences(input.as_str().unwrap())),
            "catalog_atoms" => json!(atoms_from_catalog_sources(
                &sources(&input["sources"]),
                input["max_note_atoms"].as_i64().unwrap(),
                input["max_extra_atoms_per_source"].as_i64().unwrap()
            )),
            "notes" => json!(catalog_notes_projection(&sources(input))),
            "observations" => json!(atoms_from_observations(input.as_array().unwrap())),
            "clauses" => json!(
                preface_clauses_from_payload(&input["raw"], &atoms(&input["atoms"]))
                    .map(|rows| rows.iter().map(PrefaceClause::to_dict).collect::<Vec<_>>())
            ),
            "derived" => {
                let clauses: Vec<PrefaceClause> = serde_json::from_value(input.clone()).unwrap();
                json!({"atoms":atoms_from_preface_clauses(&clauses),"interpretation":atom_from_poetic_interpretation(&clauses)})
            }
            "merge" => json!(merge_source_atoms(
                &input
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(atoms)
                    .collect::<Vec<_>>()
            )),
            "stored" => json!(source_atoms_from_materials(input)),
            "line_sources" => json!(line_source_ids_from_materials(
                &input["materials"],
                &serde_json::from_value::<Vec<String>>(input["verse_lines"].clone()).unwrap(),
                &serde_json::from_value::<HashSet<String>>(input["allowed_atom_ids"].clone())
                    .unwrap()
            )),
            op => panic!("unknown op {op}"),
        };
        if row["op"] == "clauses" && row["expected"].is_null() && !actual.is_null() {
            // 長さだけで旧契約が棄却した行も、出典と本文を保ったまま受け取る。
            let clauses = actual.as_array().unwrap();
            assert!(!clauses.is_empty());
            assert!(
                clauses
                    .iter()
                    .all(|c| !c["text"].as_str().unwrap().is_empty())
            );
            continue;
        }
        assert_eq!(
            actual, row["expected"],
            "row {i} op {} input {input}",
            row["op"]
        );
    }
}
#[test]
fn saved_source_never_reloads_or_adopts_changed_catalog_values() {
    let old = catalog_source_snapshot(
        "mob",
        "cat",
        &json!({"label":"猫","note":"保存当時の説明。"}),
        "current",
        "",
    )
    .unwrap();
    let old_atoms = atoms_from_catalog_sources(std::slice::from_ref(&old), 8, 5);
    let saved = json!({"source_atoms":old_atoms});
    let before = saved.clone();
    let changed = catalog_source_snapshot(
        "mob",
        "cat",
        &json!({"label":"別名","note":"新しくなった説明。"}),
        "current",
        "",
    )
    .unwrap();
    assert_ne!(old, changed);
    assert_eq!(source_atoms_from_materials(&saved), old_atoms);
    assert_eq!(saved, before);
}

#[test]
fn scene_length_and_clause_count_do_not_veto_valid_material() {
    let a = atoms_from_observations(&[json!({"source":"world","key":"rain","label":"雨"})]);
    let clause = |text: &str| json!({"text":text,"basis_atom_ids":[a[0].atom_id],"claim_class":"interpretive"});
    let long = "雨の気配がゆっくり遠くまで広がっていて静かな時間がいつまでも続いているように感じる";
    assert!(preface_clauses_from_payload(&json!([clause(long)]), &a).is_some());
    assert!(
        preface_clauses_from_payload(
            &json!([clause("雨"), clause("空"), clause("音"), clause("雲")]),
            &a
        )
        .is_some()
    );
}
