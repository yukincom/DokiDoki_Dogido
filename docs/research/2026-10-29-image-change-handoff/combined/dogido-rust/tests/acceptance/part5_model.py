#!/usr/bin/env python3
"""実際の連続撮影で画像差分の実モデル試験を準備・実行する。既存Javaの数値比較と、8×6マスの枠比較を区別し、共通の画像診断送信関数で要求・返答・測定記録を残す。"""
from __future__ import annotations

import argparse
import base64
import collections
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request

SUITE = Path(__file__).resolve().parent
REPO = SUITE.parents[2]
BINS = [("zero", 0, 1e-9), ("under_1", 1e-9, 1), ("1_to_2", 1, 2),
        ("2_to_5", 2, 5), ("5_to_10", 5, 10), ("10_to_25", 10, 25), ("over_25", 25, 101)]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def motion(before, after):
    """同じ撮影列の位置・向き差を数値化する。位置や向きで画像を捨てず、比較条件として記録する。"""
    return {
        "elapsed_seconds": (timestamp(after["captured_at"]) - timestamp(before["captured_at"])).total_seconds(),
        "position_delta_blocks": math.dist([before["camera_eye"][k] for k in "xyz"], [after["camera_eye"][k] for k in "xyz"]),
        "yaw_delta_degrees": abs((after["camera_yaw"] - before["camera_yaw"] + 180) % 360 - 180),
        "pitch_delta_degrees": abs(after["camera_pitch"] - before["camera_pitch"]),
    }


def adjacent_pairs(captures):
    """同boot・身体・世界のmotion_sequenceが連番の画像対だけを選ぶ。欠番を飛ばさず、撮影間隔は記録する。"""
    groups = collections.defaultdict(list)
    for path in sorted(captures.glob("*/frame.json")):
        row = load(path)
        if "motion_sequence" not in row or not (path.parent / "frame.png").is_file() or not (path.parent / "player-192.png").is_file():
            continue
        row["directory"] = str(path.parent)
        groups[(row["boot_id"], json.dumps(row["context"], sort_keys=True))].append(row)
    pairs = []
    for rows in groups.values():
        rows.sort(key=lambda row: row["captured_at"])
        for before, after in zip(rows, rows[1:]):
            if after["motion_sequence"] != before["motion_sequence"] + 1:
                continue
            moved = motion(before, after)
            if moved["elapsed_seconds"] <= 0:
                continue
            pairs.append({"id": after["capture_id"], "before_meta": before, "after_meta": after,
                          "before": before["directory"] + "/frame.png", "after": after["directory"] + "/frame.png",
                          "camera_motion": moved})
    return pairs


def measure(pairs):
    """本体のBodyViewChangeを直接使う。4枚前選択を再現せず、指定した実画像対を同じRGB計算へ通す。"""
    java_home = os.environ.get("JAVA_HOME") or subprocess.run(["/usr/libexec/java_home"], check=True, capture_output=True, text=True).stdout.strip()
    gson = os.environ.get("DOGIDO_ACCEPTANCE_GSON")
    if not gson:
        candidates = sorted((Path.home() / ".gradle/caches/modules-2/files-2.1/com.google.code.gson/gson").glob("*/*/gson-*.jar"))
        if not candidates:
            raise RuntimeError("既存Gson jarが必要")
        gson = str(candidates[-1])
    with tempfile.TemporaryDirectory(prefix="dogido-pair-metrics-") as classes:
        files = [REPO / "adapter/minecraft-fabric/src/main/java/dogido/fabric/screenshot/BodyViewChange.java",
                 SUITE / "java/dogido/fabric/screenshot/BodyViewPairMetrics.java"]
        subprocess.run([java_home + "/bin/javac", "--release", "21", "-cp", gson, "-d", classes, *map(str, files)], check=True)
        result = subprocess.run([java_home + "/bin/java", "-Djava.awt.headless=true", "-cp", classes + os.pathsep + gson,
                                 "dogido.fabric.screenshot.BodyViewPairMetrics"],
                                input=json.dumps(pairs), text=True, capture_output=True, check=True)
    return {row["id"]: row for row in json.loads(result.stdout)}


def choose(pairs):
    """返答を見る前に固定する抽出規則。同姿勢の各変化率帯から両端を最大2件、別途大きな旋回2件。共有画像を避け、足りない帯は補作しない。"""
    selected, used, coverage = [], set(), []
    def add(row, category):
        ids = {row["before_meta"]["capture_id"], row["after_meta"]["capture_id"]}
        if ids & used:
            return False
        selected.append({**row, "category": category}); used.update(ids); return True
    stable = [row for row in pairs if all(row["camera_motion"][key] <= 1e-6 for key in
              ["position_delta_blocks", "yaw_delta_degrees", "pitch_delta_degrees"])
              and row["camera_motion"]["elapsed_seconds"] <= 15]
    for name, low, high in BINS:
        group = sorted([row for row in stable if low <= row["metrics"]["changed_area_percent"] < high],
                       key=lambda row: (row["metrics"]["changed_area_percent"], row["metrics"]["mean_rgb_difference_0_255"], row["id"]))
        count = 0
        for order in [group, list(reversed(group))]:
            for row in order:
                if add(row, name): count += 1; break
        coverage.append({"bin": name, "eligible_pairs": len(group), "selected": count})
    moved = sorted([row for row in pairs if row["camera_motion"]["yaw_delta_degrees"] >= 45 and
                    row["camera_motion"]["elapsed_seconds"] <= 15],
                   key=lambda row: (row["metrics"]["changed_area_percent"], row["id"]))
    for order in [moved, list(reversed(moved))]:
        for row in order:
            if add(row, "camera_turn"): break
    return selected, coverage


def prepare(args):
    """画像を1画素も変更せず専用出力へコピーし、元hash・実測値・固定プロンプトを保存する。モデルは呼ばない。"""
    if args.output.exists():
        raise ValueError("出力先は新規ディレクトリにしてください")
    pairs = adjacent_pairs(args.captures)
    if not pairs:
        raise ValueError("実際の連続撮影画像対が見つからない")
    metrics = measure(pairs)
    for pair in pairs:
        pair["metrics"] = metrics[pair["id"]]
    selected, coverage = choose(pairs)
    if not selected:
        raise ValueError("比較条件に該当する画像対がない")
    args.output.mkdir(parents=True)
    spec = load(SUITE / "part5_model_prompt.json")
    prompts = load(args.prompts)
    # 本体のenvironment(false,false)と同じ共有人格・通常口調・観測・ambient・JSON契約。身体行動の試験ではない。
    system = "\n".join([prompts["base"], prompts["modes"]["normal"], prompts["grounding"],
                         prompts["dialogue"]["ambient"], prompts["choice_contract"], spec["pair_instruction"]])
    manifest = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "evidence": "real_consecutive_capture_pairs", "source_images_modified": False,
                "system": system, "spec": spec, "coverage": coverage, "candidate_pair_count": len(pairs), "cases": [],
                "scope": "画像対の独立診断。本体の発話機会・4枚前比較・連続会話の反復抑制は検証しない。",
                "sources": {str(path): sha(path) for path in [args.prompts, SUITE / "part5_model_prompt.json",
                    REPO / "adapter/minecraft-fabric/src/main/java/dogido/fabric/screenshot/BodyViewChange.java",
                    SUITE / "java/dogido/fabric/screenshot/BodyViewPairMetrics.java", Path(__file__)]}}
    for number, pair in enumerate(selected, 1):
        case = {"id": f"pair_{number:02}", "category": pair["category"], "metrics": pair["metrics"],
                "camera_motion": pair["camera_motion"], "frames": [],
                "aim": "実測した変化率と撮影姿勢が同じ画像対で、差分量・領域を添える効果だけを比べる。",
                "review": spec["review"]}
        directory = args.output / case["id"]; directory.mkdir()
        for side in ["before", "after"]:
            meta = pair[side + "_meta"]
            source = Path(meta["directory"])
            for filename, key in [("frame.png", "image_sha256"), ("player-192.png", "annotated_sha256")]:
                path = source / filename
                if sha(path) != meta[key]:
                    raise ValueError(f"元画像hash不一致: {path}")
                target = directory / (side + ("-native.png" if filename == "frame.png" else "-model.png"))
                target.write_bytes(path.read_bytes())
                assert sha(target) == meta[key]
            save(directory / (side + ".json"), meta)
            case["frames"].append({"label": side, "captured_at": meta["captured_at"],
                "capture_id": meta["capture_id"], "motion_sequence": meta["motion_sequence"],
                "original_path": str(source), "native": str((directory / (side + "-native.png")).relative_to(args.output)),
                "native_sha256": meta["image_sha256"], "model_image": str((directory / (side + "-model.png")).relative_to(args.output)),
                "model_sha256": meta["annotated_sha256"], "width": meta["annotated_width"], "height": meta["annotated_height"]})
        manifest["cases"].append(case)
    save(args.output / "manifest.json", manifest)
    save(args.output / "candidate-measurements.json", [{k: pair[k] for k in ["id", "metrics", "camera_motion"]} for pair in pairs])
    previews = [payload(manifest, case, condition, spec["seeds"][0], args.output, preview=True)
                for case in manifest["cases"] for condition in spec["conditions"]]
    save(args.output / "request-previews.json", previews)
    print(f"準備完了: 実画像対{len(selected)}件、全{len(pairs)}対から抽出、モデル呼出し0回")
    report(args.output, manifest, [])


def payload(manifest, case, condition, seed, root, preview=False):
    """共通指示・前後画像・時刻・姿勢情報を固定する。数値試験は差分情報だけ、8×6試験は後画像の検出枠と指定メッセージだけを対照条件から変える。"""
    spec = manifest["spec"]
    context = {**spec["shared_context"], "frame_order": ["before", "after"],
               "elapsed_seconds": case["camera_motion"]["elapsed_seconds"], "camera_motion": case["camera_motion"]}
    if manifest.get("experiment") == "grid_location_merged":
        context["brightness_detection"] = case["brightness_message"]
        if condition == "location_text":
            context["change_location_basis"] = "画像を横8×縦6に分けた位置。行は上から、列は左から数える。"
            context["change_locations"] = case["location_messages"]
        elif condition == "merged_outline":
            context["change_detection"] = spec["merged_message"]
        else:
            raise ValueError("位置・結合枠試験の未知の条件")
    elif manifest.get("experiment") == "grid_8x6":
        if condition == "grid_hint":
            context["change_detection"] = spec["changed_message"] if case["grid_detection"]["changed_cell_count"] else spec["unchanged_message"]
    elif condition != "images_only":
        context["pixel_comparison"] = {"definition": spec["metric_definition"], **{key: case["metrics"][key] for key in
            ["grid_width", "grid_height", "changed_cell_count", "changed_area_percent", "mean_rgb_difference_0_255", "cell_difference_threshold_exclusive"]}}
    if condition == "images_amount_and_regions":
        # 大領域から8件を渡す。打切りを隠さず、除いた領域数とセル数も残す。物体名や正解ラベルは渡さない。
        regions = case["metrics"]["regions"]
        context["pixel_comparison"]["region_definition"] = spec["region_definition"]
        context["pixel_comparison"]["regions"] = regions[:8]
        context["pixel_comparison"]["omitted_region_count"] = max(0, len(regions) - 8)
        context["pixel_comparison"]["omitted_changed_cells"] = sum(row["changed_cells"] for row in regions[8:])
    blocks = []
    for frame in case["frames"]:
        variant = "merged_model" if condition == "merged_outline" else "grid_model" if condition == "grid_hint" else "model"
        prefix = variant if frame["label"] == "after" else "model"
        key, hash_key = prefix + "_image", prefix + "_sha256"
        path = root / frame[key]
        if sha(path) != frame[hash_key]:
            raise ValueError("送信画像hash不一致")
        blocks.append({"type": "text", "text": frame["label"] + " / " + frame["captured_at"]})
        blocks.append({"type": "image_url", "image_url": {"url": ("local-image:" + frame[key] if preview else
            "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode())}})
    blocks.append({"type": "text", "text": json.dumps(context, ensure_ascii=False)})
    return {**spec["parameters"], "seed": seed,
            "messages": [{"role": "system", "content": manifest["system"]}, {"role": "user", "content": blocks}]}


def prepare_grid(args):
    """前回固定した実画像対を再利用する。試験間で元画像・共通指示を変えず、変化率を要求から除き、マス枠と指定メッセージの効果を測る。数値は計測記録にだけ保存する。"""
    import part5_grid
    if args.output.exists() or args.source_trial is None:
        raise ValueError("新規出力先と--source-trialが必要")
    source = load(args.source_trial / "manifest.json")
    if source.get("experiment") == "grid_8x6":
        raise ValueError("元の実画像対試験を指定してください")
    args.output.mkdir(parents=True)
    manifest = {**source, "experiment": "grid_8x6", "created_at": datetime.now(timezone.utc).isoformat(),
        "source_trial": str(args.source_trial), "source_manifest_sha256": sha(args.source_trial / "manifest.json"),
        "source_images_modified": False, "model_overlay_added": True, "grid_config": part5_grid.CONFIG,
        "scope": "実画像13組の独立診断。8×6の差分枠と検出メッセージを比較。連続会話・実運用の検証ではない。"}
    manifest["spec"] = {k: v for k, v in source["spec"].items() if k not in {"metric_definition", "region_definition"}}
    manifest["spec"].update(conditions=["images_only", "grid_hint"],
        purpose="8×6の局所差と明るさの閾値で枠を表示し、枠なしと比較する。変化率を渡さない。",
        changed_message="変化領域をオレンジ色の枠で表示しています。", unchanged_message="変化がない")
    manifest["sources"] = {str(path): sha(path) for path in [Path(__file__), Path(part5_grid.__file__)]}
    for case in manifest["cases"]:
        case.pop("metrics", None)
        case["aim"] = "マス内の大きな局所差と明確な明るさ差を検出し、枠付き画像または『変化がない』による応答の変化を比較する。"
        shutil.copytree(args.source_trial / case["id"], args.output / case["id"])
        frames = case["frames"]
        for frame in frames:
            for path_key, hash_key in [("native", "native_sha256"), ("model_image", "model_sha256")]:
                if sha(args.output / frame[path_key]) != frame[hash_key]:
                    raise ValueError("元の実撮影画像hash不一致")
        detection = part5_grid.measure(args.output / frames[0]["native"], args.output / frames[1]["native"])
        case["grid_detection"] = detection
        target = args.output / case["id"] / "after-grid.png"
        part5_grid.outline(args.output / frames[1]["model_image"], target, detection)
        frames[1].update(grid_model_image=str(target.relative_to(args.output)), grid_model_sha256=sha(target))
    save(args.output / "manifest.json", manifest)
    previews = [payload(manifest, case, condition, manifest["spec"]["seeds"][0], args.output, preview=True)
                for case in manifest["cases"] for condition in manifest["spec"]["conditions"]]
    save(args.output / "request-previews.json", previews)
    for path in [Path(__file__), Path(part5_grid.__file__)]:
        shutil.copy2(path, args.output / path.name)
    report(args.output, manifest, [])
    print(f"8×6試験の準備完了: {len(manifest['cases'])}組。実モデル呼出し0回")


def parse_response(response):
    """JSON本文とコード枠付きJSONを区別して読む。コード枠は形式違反として記録し、内容の比較から除外しない。出力の補作・再生成はしない。"""
    choice = response["choices"][0]
    if choice.get("finish_reason") != "stop":
        raise ValueError("正常終了していない出力")
    raw = choice["message"]["content"].strip()
    form = "json"
    if raw.startswith("```json\n") and raw.endswith("\n```"):
        raw, form = raw[8:-4], "fenced_json"
    elif raw.startswith("```\n") and raw.endswith("\n```"):
        raw, form = raw[4:-4], "fenced_json"
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or parsed.get("action") not in {"speak", "silent"} or not isinstance(parsed.get("speech"), str):
        raise ValueError("action/speech形式不正")
    if parsed["action"] == "speak" and not parsed["speech"].strip():
        raise ValueError("speakの本文が空")
    if parsed["action"] == "silent" and parsed["speech"] != "":
        raise ValueError("silentの本文が空でない")
    return parsed, form


def prepare_grid_presentation(args):
    """前回の検出結果を再計測して同一性を確かめ、変化ありの画像だけを比較する。共通の明るさ判定文を添え、枠なしの位置説明と共有辺を消した枠の2条件を作る。"""
    import part5_grid
    import part5_grid_presentation as presentation
    if args.output.exists() or args.source_trial is None:
        raise ValueError("新規出力先と--source-trialが必要")
    source = load(args.source_trial / "manifest.json")
    if source.get("experiment") != "grid_8x6" or source["grid_config"] != part5_grid.CONFIG:
        raise ValueError("前回と同じ8×6検出設定が必要")
    detector_path = Path(part5_grid.__file__).resolve()
    if sha(detector_path) != source["sources"][str(detector_path)]:
        raise ValueError("前回から検出コードが変わっている")
    args.output.mkdir(parents=True)
    manifest = {**source, "experiment": "grid_location_merged", "created_at": datetime.now(timezone.utc).isoformat(),
        "source_trial": str(args.source_trial), "source_manifest_sha256": sha(args.source_trial / "manifest.json"),
        "cases": [case for case in source["cases"] if case["grid_detection"]["changed_cell_count"]],
        "scope": "検出方法を固定した位置説明と結合枠の比較。明るさ判定文は共通。変化なしの画像は再試験しない。"}
    manifest["spec"] = {**source["spec"], "conditions": ["location_text", "merged_outline"],
        "purpose": "変化ありの同じ実画像で、具体的な位置を文で渡す場合と、隣接枠を結合する場合を比較する。",
        "merged_message": "隣接する変化領域の枠を結合し、外周だけオレンジ色で表示しています。"}
    manifest["sources"] = {str(path): sha(path) for path in [Path(__file__), detector_path, Path(presentation.__file__)]}
    for case in manifest["cases"]:
        shutil.copytree(args.source_trial / case["id"], args.output / case["id"])
        f = case["frames"]
        for frame in f:
            for pk, hk in [("native", "native_sha256"), ("model_image", "model_sha256")]:
                if sha(args.output / frame[pk]) != frame[hk]:
                    raise ValueError("原画像hash不一致")
        measured = part5_grid.measure(args.output / f[0]["native"], args.output / f[1]["native"], manifest["grid_config"])
        if measured != case["grid_detection"]:
            raise ValueError("前回と検出結果が異なる")
        components = presentation.regions(measured)
        case.update(location_messages=[presentation.location_text(c) for c in components],
                    brightness_message=presentation.brightness_text(measured), merged_region_count=len(components),
                    aim="検出マスと明るさ判定を固定し、位置の文と結合枠で認識・応答がどう変わるか比較する。")
        target = args.output / case["id"] / "after-merged.png"
        presentation.outline_merged(args.output / f[1]["model_image"], target, measured, manifest["grid_config"])
        f[1].update(merged_model_image=str(target.relative_to(args.output)), merged_model_sha256=sha(target))
    save(args.output / "manifest.json", manifest)
    save(args.output / "request-previews.json", [payload(manifest, c, cond, manifest["spec"]["seeds"][0], args.output, True)
        for c in manifest["cases"] for cond in manifest["spec"]["conditions"]])
    for path in [Path(__file__), detector_path, Path(presentation.__file__)]:
        shutil.copy2(path, args.output / path.name)
    report(args.output, manifest, [])
    print(f"位置・結合枠試験の準備完了: {len(manifest['cases'])}組。検出判定は前回と同一。")


def report(root, manifest, rows):
    """発話率と返答の原文を並べる。発話率を過敏反応率と同一視せず、内容判定は画像と照合するため未採点で残す。"""
    lines = ["目的: 実撮影だけで差分量・領域の効果を比較する。発話率は過敏反応率ではなく、画像にない変化の断定や誇張を内容で確認する。", "",
             "| ケース | 変化セル割合 | 平均差 / 255 | 視点 | 前 → 後 |", "|---|---:|---:|---|---|"]
    presentation_trial = manifest.get("experiment") == "grid_location_merged"
    grid_trial = manifest.get("experiment") in {"grid_8x6", "grid_location_merged"}
    if grid_trial:
        lines = ["目的: 8×6の各マスで大きな局所差・明るさ差だけを検出し、枠とメッセージを添えた効果を実画像で比較する。変化率はモデルへ渡さない。", "",
            "| ケース | 検出マス | 局所差 | 明るさ差 | 通知 | 前 → 後 |", "|---|---:|---:|---:|---|---|"]
    for case in manifest["cases"]:
        if grid_trial:
            d = case["grid_detection"]; f = case["frames"]
            notice = case["brightness_message"] if presentation_trial else "変化領域を枠で表示" if d["changed_cell_count"] else "変化がない"
            image_key = "merged_model_image" if presentation_trial else "grid_model_image"
            lines.append(f"| {case['id']} | {d['changed_cell_count']} | {d['local_cell_count']} | {d['brightness_cell_count']} | {notice} | [前]({f[0]['model_image']}) → [後]({f[1][image_key]}) |")
            continue
        pose = "旋回あり" if case["category"] == "camera_turn" else "同姿勢"
        frames = case["frames"]
        lines.append(f"| {case['id']} | {case['metrics']['changed_area_percent']:.3f}% | {case['metrics']['mean_rgb_difference_0_255']:.3f} | {pose} | [前]({frames[0]['native']}) → [後]({frames[1]['native']}) |")
    if presentation_trial:
        lines += ["", "検出コード・閾値・各マスの判定を前回から固定。共通の明るさ判定文を添え、枠なしの位置説明と隣接枠の外周だけを比較する。変化率は渡さない。", ""]
    elif grid_trial:
        lines += ["", "閾値を超えたマスだけに1pxのオレンジ枠を追加。全マスの線・数値・変化率は描かない。過去発話なしの独立診断で、反復抑制は未評価。", ""]
    else:
        lines += ["", f"実画像対{len(manifest['cases'])}件。1セル=約0.174%。微小色差は領域集計から除くが、この校正試験では低変化率もモデルへ渡す。実運用の棄却閾値は未決定。", "",
              "全条件で同じ撮影済み192px画像を使用。元画像は加工しない。過去発話なしの独立試験なので、会話履歴を使った反復抑制は未評価。", ""]
    if rows:
        lines += ["| ケース | 条件 | seed | action | 返答 | 外形 | 秒 |", "|---|---|---:|---|---|---|---:|"]
        for row in rows:
            result = row.get("parsed") or {}
            speech = str(result.get("speech", row.get("error", "形式不正"))).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {row['case_id']} | {row['condition']} | {row['seed']} | {result.get('action', 'invalid')} | {speech} | {row.get('output_format', 'invalid')} | {row.get('elapsed_seconds', '')} |")
    else:
        lines += ["実モデルは未実行。request-previews.jsonで全要求を確認できる。"]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args):
    """確認済みの固定要求を既存ローカルAPIへ逐次送信する。再試行はせず、生返答と使用量を保存する。ゲームの接続・音声・記憶には接続しない。"""
    manifest = load(args.output / "manifest.json")
    url = urllib.parse.urlparse(args.base_url)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("この試験は既存のローカルAPI専用")
    helper_path = REPO / "docs/research/2026-10-20-vision-resolution/run_experiment.py"
    helper_spec = importlib.util.spec_from_file_location("dogido_existing_vision_probe", helper_path)
    helper = importlib.util.module_from_spec(helper_spec); helper_spec.loader.exec_module(helper)
    result_path = args.output / "results.jsonl"
    if result_path.exists():
        raise ValueError("結果は上書きしない。新しく準備した出力先を指定してください")
    with urllib.request.urlopen(args.base_url.rstrip("/") + "/models", timeout=5) as response:
        save(args.output / "model-info.json", json.load(response))
    rows = []
    with result_path.open("x", encoding="utf-8") as stream:
        for n, case in enumerate(manifest["cases"]):
            for seed in manifest["spec"]["seeds"]:
                conditions = manifest["spec"]["conditions"]
                # 条件順を循環させ、常に同じ条件が最初に実行される偏りを減らす。並列要求は出さない。
                offset = (n + seed) % len(conditions)
                for condition in conditions[offset:] + conditions[:offset]:
                    request = payload(manifest, case, condition, seed, args.output)
                    row = {"case_id": case["id"], "condition": condition, "seed": seed,
                           "request_preview": payload(manifest, case, condition, seed, args.output, preview=True),
                           "content_review": "pending"}
                    if "metrics" in case:
                        row["changed_area_percent"] = case["metrics"]["changed_area_percent"]
                    if "grid_detection" in case:
                        row["changed_cell_count"] = case["grid_detection"]["changed_cell_count"]
                    try:
                        response, elapsed = helper.send_payload(args.base_url.rstrip("/") + "/chat/completions", request)
                        row.update(response=response, elapsed_seconds=elapsed)
                        row["parsed"], row["output_format"] = parse_response(response)
                    except Exception as error:
                        row["error"] = f"{type(error).__name__}: {error}"
                    rows.append(row); stream.write(json.dumps(row, ensure_ascii=False) + "\n"); stream.flush()
                    print(case["id"], condition, seed, json.dumps(row.get("parsed", row.get("error")), ensure_ascii=False), flush=True)
                    report(args.output, manifest, rows)
    print("完了。内容レビューは未採点。REPORT.mdと実画像を照合してください。")
    raise SystemExit(1 if any("error" in row for row in rows) else 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "prepare-grid", "prepare-grid-presentation", "run"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--captures", type=Path, default=Path.home() / "Library/Application Support/minecraft/dogido-vision/captures")
    parser.add_argument("--prompts", type=Path, default=REPO / "dogido_server/llm/companion_prompts.json")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--source-trial", type=Path)
    args = parser.parse_args()
    {"prepare": prepare, "prepare-grid": prepare_grid, "prepare-grid-presentation": prepare_grid_presentation, "run": run}[args.mode](args)


if __name__ == "__main__":
    main()
