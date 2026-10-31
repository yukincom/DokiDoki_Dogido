#!/usr/bin/env python3
"""Bounded, independent haiku experiment. Never imports or starts the live server.

Python handles experiment fixtures, prompt transport, and result recording only.
The three model responses own creative selection and composition. No semantic
filter, automatic retry, memory write, audio, or live settings change is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request


CASES = [
    ("01_black_yellow", "黒曜石とタンポポ", "ses_968b6d165332", 2),
    ("02_beach_morning", "砂浜の朝と松明", "ses_4d9f78d6a5e9", 1),
    ("03_oak_clock", "オークの原木と時計", "ses_b74053a392eb", 1),
    ("04_underground_torch", "地下の原木と松明", "ses_7d5a6a5f7cb8", 1),
    ("05_desert_apple", "砂漠と金のリンゴ", "ses_8953557ee025", 1),
]
STAGES = ("preface", "select", "poem")
DERIVED = {"preface_clause", "poetic_interpretation"}
EXPERIMENT = "preface_separation_v2_three_materials"
POSITIONS = ("上五", "中七", "下五")
EXPECTATIONS = {
    "01_black_yellow": "黒曜石とタンポポを含む候補から3材料以上を前置きで選び、その中の別々の3材料を各行へ割り当てる。花とタンポポなど同じ対象の言い直しで2行を埋めない。",
    "02_beach_morning": "朝の観測と朝の会話が併存しても、別IDというだけで同じ朝の説明を繰り返さない。3行がそれぞれ割当材料による別の内容を持つ。",
    "03_oak_clock": "時計の名称と説明は一つの材料として数え、他の2材料と別々の行へ割り当てる。木の実や時計の音など、選択資料から読み取れないものへ置き換えない。",
    "04_underground_torch": "原木・松明・頭上の石などの候補から別々の3材料を選ぶ。3行が各割当材料を表し、前置きだけの印象で材料のない行を埋めない。",
    "05_desert_apple": "砂漠の環境と持ち物の候補から別々の3材料を選ぶ。各行が割当材料を表し、同じ暑さや金色の言い直しだけで三行を構成しない。",
}

PREFACE = """ドギドとして、いまの材料をひと息ながめてほしい。
川柳の芯になりそうな『取り合わせ』か『その場の気配』をひとつだけ拾う。
材料にある周りのブロックや生きもの、土地の気配にまず目を向けてな。持ち物も、その場とのつながりが面白ければ取り合わせてええで。
大げさな矛盾は要らない。平凡でも、ふっと心に残る一点でよい。
材料の意味を変えず、具体的な一点を短く書く。
description は川柳を考えている間にそのまま声へ出す前置きや。自然な関西弁の一文にし、固い分析報告ではなく、相棒が景色を感じた言葉として書いてな。
三行の句に使うため、前置きで取り上げる元の材料を3つ以上選んでな。
同じ対象の名前・説明・言い換えを別々の材料として数えず、選んだ材料どうしの取り合わせを前置きで表してな。
その材料を、一覧にある material_id で selected_material_ids に記録してな。
材料の選択と前置きは同じ返答で返してな。

【いまの材料】
{materials}

返事はJSONオブジェクト一つだけ。
形: {{"description":"前置きの一文","selected_material_ids":["m01","m02","m03"]}}
"""

SELECT = """ここにあるのは、ドギドが前置きで選んだ元の材料だけや。
この中から、別々の元材料をちょうど3つ選んでな。
上五・中七・下五に一つずつ割り当て、各行の芯にする材料を決めてな。同じ材料を二つの行へ割り当てんといてな。
名前と説明は同じ材料の情報や。説明文を別の物として増やさんといてな。
ここでは材料だけを選び、句や前置きの言い換えは作らんといてな。

【前置きで選んだ元の材料】
{materials}

返事はJSONオブジェクト一つだけ。一覧にあるmaterial_idを返してな。
line_indexは上五=0、中七=1、下五=2や。
形: {{"line_materials":[{{"line_index":0,"material_id":"m01"}},{{"line_index":1,"material_id":"m02"}},{{"line_index":2,"material_id":"m03"}}]}}
"""

POEM = """プレイヤーと並んで景色を見て、ふっと川柳を一句詠んでな。
まず【各行に割り当てた元材料】を確かめ、次に【材料の調理方針となる前置き】から、どういう取り合わせ・対比・印象を表したかったかを確かめてな。
前置きは材料をどう料理するかの指針や。前置きの文章そのものを一個の材料として数えたり、前置きに出るだけの対象を材料一覧へ足したりはせんといてな。
上五・中七・下五は、それぞれ割り当てられた一つの元材料を芯にして詠んでな。別の行の材料を言い直すことで、自分の行を埋めんといてな。
三つの元材料を、前置きが示す調理方針で一つの句へつなげてな。
材料名の全文を写す必要はない。材料から自然に浮かぶ比喩・余情・印象も使ってええで。
造語や崩れた文を避け、耳で一度聞いて意味の通る自然な現代日本語にしてな。

【各行に割り当てた元材料】
{materials}

【材料の調理方針となる前置き】
{preface}

上五・中七・下五の三行。音数は五・七・五を目指し、各行の許容範囲は±一音。かなだけで書いてな。
返事はJSONオブジェクト一つだけ。句の三行だけを返し、材料番号や解説は要らへんで。
形: {{"lines":["ごおんのく","ななおんのく","ごおんのく"]}}
"""


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ingredients(atoms):
    """Only original source data are eligible; derived preface prose is excluded."""
    groups = {}
    for atom in atoms:
        if (atom.get("kind") in DERIVED or atom.get("basis_atom_ids")
                or atom.get("source_ref", "").startswith("preface:")):
            continue
        ref = atom["source_ref"]
        group = groups.setdefault(ref, {"source_ref": ref, "facts": []})
        fact = {k: atom[k] for k in ("text", "claim_class", "claim_scopes")}
        if fact not in group["facts"]:
            group["facts"].append(fact)
    return [{"material_id": f"m{i:02d}", **g} for i, g in enumerate(groups.values(), 1)]


def choose(materials, response):
    """Validate references, never infer or reclassify creative choices from prose."""
    ids = response.get("selected_material_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids):
        raise ValueError("selected_material_ids must be a nonempty string list")
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate material identifier")
    by_id = {m["material_id"]: m for m in materials}
    if any(i not in by_id for i in ids):
        raise ValueError("selection is not a subset of the offered ingredients")
    return [by_id[i] for i in ids]


def preface_materials(materials, response):
    selected = choose(materials, response)
    if len({m["source_ref"] for m in selected}) < 3:
        raise ValueError("preface must select at least three distinct original materials")
    return selected


def assign_lines(materials, response):
    rows = response.get("line_materials")
    if not isinstance(rows, list) or len(rows) != 3 or any(not isinstance(r, dict) for r in rows):
        raise ValueError("expected exactly three line material assignments")
    if any(type(r.get("line_index")) is not int for r in rows) or {r["line_index"] for r in rows} != {0, 1, 2}:
        raise ValueError("line indices must cover upper, middle, lower exactly once")
    rows = sorted(rows, key=lambda r: r["line_index"])
    selected = choose(materials, {"selected_material_ids": [r.get("material_id") for r in rows]})
    if len({m["source_ref"] for m in selected}) != 3:
        raise ValueError("three material IDs must represent three distinct original sources")
    return [{"line_index": i, "position": POSITIONS[i], "material": m} for i, m in enumerate(selected)]


def parse_response(text):
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("response must be a JSON object")
    return result


def mora_counts(lines):
    # Diagnostic only: the Rust rule for already-kana strings, without normalization.
    # This experiment never rejects or regenerates a poem because of these counts.
    return [sum(i == 0 or c not in "ゃゅょャュョぁぃぅぇぉァィゥェォゎヮ"
                for i, c in enumerate("".join(s.split()))) for s in lines]


def freeze(repo, out):
    base_path = repo / "dogido_server/llm/companion_prompts.json"
    write(out / "source.json", {
        "experiment": EXPERIMENT, "base": read(base_path)["base"],
        "base_source": str(base_path),
        "base_source_sha256": hashlib.sha256(base_path.read_bytes()).hexdigest(),
        "templates": {"preface": PREFACE, "select": SELECT, "poem": POEM},
        "scope": "independent real-model experiment; no production integration",
        "sampling": {"preface": [0.15, 512], "select": [0.2, 256], "poem": [0.6, 192]},
    })
    cases = []
    for name, label, sid, line in CASES:
        p = repo / ".dogido_memory/rust-migration/sessions" / sid / "long_term/haiku_entries.jsonl"
        raw = p.read_text().splitlines()[line - 1]
        row = json.loads(raw)
        cases.append({"id": name, "label": label, "source_path": str(p),
                      "source_line": line, "source_row_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                      "old_poem": row["text"], "materials": ingredients(row["materials_snapshot"]["source_atoms"]),
                      "expectation": EXPECTATIONS[name],
                      "pass_criteria": {"structure": "前置きで3材料以上、後段でその部分集合から3つの別材料を各行へ一つずつ割当。前置きは材料候補に入らず、句生成の調理方針欄だけに入る。",
                                        "content": "前置きが選択材料を取り上げ、生成句の各行に割当材料の内容がある。同じ対象・性質の言い直しで別の行を埋めず、意味が通る。音数だけで内容を合格にしない。"},
                      "verification_scope": "保存材料を入力にした実モデル3段階の独立試験。構造は自動検査、内容は全句を読んで別途判定。本体・音声・Minecraftは通さない。"})
    write(out / "cases.json", cases)
    write(out / "source-code.json", {"probe.py": Path(__file__).read_text(),
          "test_probe.py": Path(__file__).with_name("test_probe.py").read_text()})
    (out / "templates.md").write_text("# 全段階のプロンプト全文\n\n"
        "前置き・句生成のsystemには次の共有人格を付けます。材料選択のsystemは後述の一文のみです。\n\n"
        + read(out / "source.json")["base"] + "\n\n"
        + "\n\n".join(f"## {s}\n\n```text\n{t}\n```" for s, t in
                           [("preface", PREFACE), ("select", SELECT), ("poem", POEM)])
        + "\n\n材料選択のsystem: 川柳に使う元の材料を選ぶ。返答はJSONのみ。\n")


def cases_for_stage(out, stage):
    """Keep failed cases recorded; only advance cases whose preceding stages passed structure checks."""
    cases = []
    for case in read(out / "cases.json"):
        for prior_stage in STAGES[:STAGES.index(stage)]:
            prior = read(out / "responses" / prior_stage / (case["id"] + ".json"))
            if prior["status"] != "complete":
                break
        else:
            cases.append(case)
    return cases


def prepare(out, stage, model):
    config = read(out / "source.json")
    if config["experiment"] != EXPERIMENT:
        raise ValueError("experiment version mismatch; preserve the earlier run")
    requests = []
    original_indices = {c["id"]: i for i, c in enumerate(read(out / "cases.json"))}
    for case in cases_for_stage(out, stage):
        index = original_indices[case["id"]]
        materials = case["materials"]
        preface = None
        assignments = None
        if stage != "preface":
            prior = read(out / "responses/preface" / (case["id"] + ".json"))
            if prior["status"] != "complete":
                raise ValueError("cannot advance a failed preface")
            a = prior["parsed"]
            materials = preface_materials(materials, a)
            preface = a["description"]
        if stage == "poem":
            prior = read(out / "responses/select" / (case["id"] + ".json"))
            if prior["status"] != "complete":
                raise ValueError("cannot advance a failed material selection")
            b = prior["parsed"]
            assignments = assign_lines(materials, b)
            materials = [r["material"] for r in assignments]
        user = config["templates"][stage].format(
            materials=json.dumps(assignments if stage == "poem" else materials, ensure_ascii=False, indent=2), preface=preface)
        system = {"preface": config["base"] + "\n材料から、一句の芯になる取り合わせを感じ取る。返答はJSONのみ。",
                  "select": "川柳に使う元の材料を選ぶ。返答はJSONのみ。",
                  "poem": config["base"] + "\n材料に根拠のある、自然な日本語の川柳を一句詠む。返答はJSONのみ。"}[stage]
        temperature, tokens = config["sampling"][stage]
        payload = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "temperature": temperature, "max_tokens": tokens, "seed": 42 + index, "stream": False,
                   "chat_template_kwargs": {"enable_thinking": False}}
        record = {"case_id": case["id"], "label": case["label"], "stage": stage,
                  "offered_materials": materials, "poetic_direction": preface if stage == "poem" else None,
                  "line_assignments": assignments,
                  "payload": payload, "request_sha256": hashlib.sha256(canonical(payload).encode()).hexdigest()}
        write(out / "requests" / stage / (case["id"] + ".json"), record)
        requests.append(record)
    preview = [f"# {stage} — 送信する{len(requests)}ケースのプロンプト全文\n",
               "前段の構造条件が不合格のケースには後段要求を送らない。失敗は元の応答記録に保持する。\n"]
    for r in requests:
        preview.append(f"## {r['label']}\n\nSHA256: `{r['request_sha256']}`\n")
        for m in r["payload"]["messages"]:
            preview.append(f"### {m['role']}\n\n```text\n{m['content']}\n```\n")
    (out / f"prompts-{stage}.md").write_text("\n".join(preview))
    print(str(out / f"prompts-{stage}.md"))


def run(out, stage, base_url):
    # Preflight all paths before spending any model requests.
    if read(out / "source.json")["experiment"] != EXPERIMENT:
        raise ValueError("experiment version mismatch; preserve the earlier run")
    cases = cases_for_stage(out, stage)
    for case in cases:
        if (out / "responses" / stage / (case["id"] + ".json")).exists():
            raise ValueError("stage already has a result; do not silently repeat a model run")
    if stage == "preface":
        with urllib.request.urlopen(base_url.removesuffix("/v1") + "/props", timeout=15) as f:
            props = json.load(f)
        write(out / "model.json", {k: props.get(k) for k in
              ["model_alias", "model_path", "model_ftype", "build_info", "default_generation_settings"]})
    errors = []
    for case in cases:
        request_path = out / "requests" / stage / (case["id"] + ".json")
        record = read(request_path)
        payload = record["payload"]
        if hashlib.sha256(canonical(payload).encode()).hexdigest() != record["request_sha256"]:
            raise ValueError("prepared request was changed")
        result = {"case_id": case["id"], "stage": stage, "request_sha256": record["request_sha256"]}
        started = time.monotonic()
        try:
            req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as f:
                raw = json.load(f)
            result["raw_response"] = raw
            choice = raw["choices"][0]
            result["finish_reason"] = choice.get("finish_reason")
            parsed = parse_response(choice["message"]["content"])
            result["parsed"] = parsed
            if stage == "preface":
                result["selected_materials"] = preface_materials(record["offered_materials"], parsed)
                if not isinstance(parsed.get("description"), str) or not parsed["description"].strip():
                    raise ValueError("empty preface")
            elif stage == "select":
                result["line_assignments"] = assign_lines(record["offered_materials"], parsed)
                result["selected_materials"] = [r["material"] for r in result["line_assignments"]]
            else:
                lines = parsed.get("lines")
                if not isinstance(lines, list) or len(lines) != 3 or any(not isinstance(s, str) or not s.strip() for s in lines):
                    raise ValueError("expected three nonempty lines")
                kana_only = all(all(c.isspace() or '\u3041' <= c <= '\u309f' or '\u30a1' <= c <= '\u30ff' for c in s) for s in lines)
                result["diagnostics"] = {"kana_only": kana_only, "mora_counts": mora_counts(lines) if kana_only else None}
            result["status"] = "complete"
        except Exception as exc:
            result["status"] = "failed"
            result["error"] = f"{type(exc).__name__}: {exc}"
            errors.append(case["id"])
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write(out / "responses" / stage / (case["id"] + ".json"), result)
        print(json.dumps({k: result[k] for k in ["case_id", "stage", "status", "elapsed_seconds", "parsed", "diagnostics", "error"] if k in result}, ensure_ascii=False), flush=True)
    if errors:
        raise SystemExit("Failed cases recorded without retry: " + ", ".join(errors))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["freeze", "prepare", "run"])
    p.add_argument("--repo", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--stage", choices=STAGES)
    p.add_argument("--model", default="default_model")
    p.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    args = p.parse_args()
    if args.action == "freeze":
        if not args.repo:
            p.error("freeze requires --repo")
        freeze(args.repo, args.out)
    elif args.action == "prepare":
        if not args.stage:
            p.error("prepare requires --stage")
        prepare(args.out, args.stage, args.model)
    else:
        if not args.stage:
            p.error("run requires --stage")
        run(args.out, args.stage, args.base_url)


if __name__ == "__main__":
    main()
