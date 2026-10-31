#!/usr/bin/env python3
"""見込み: 三状態・固定実画像・通信JSONでJava移植の境界を検査できる。合格: JUnit全件、13組624マス・26縮小hash、wire4ケースが一致。範囲: 本体Javaの比較・縮小・符号化。描画、HTTP、モデルは未確認。"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

from java_wire import REPO, TEST_REPO, WIRE_PRODUCTION, run_fixture


def junit_classpath():
    override = os.environ.get("DOGIDO_ACCEPTANCE_JUNIT_CP")
    if override:
        jars = [Path(value) for value in override.split(os.pathsep)]
        if not all(path.is_file() for path in jars):
            raise SystemExit("DOGIDO_ACCEPTANCE_JUNIT_CP内のjarが見つかりません")
        return jars
    cache = Path.home() / ".gradle/caches/modules-2/files-2.1"
    coordinates = [
        ("org.junit.jupiter", "junit-jupiter-api"), ("org.junit.jupiter", "junit-jupiter-engine"),
        ("org.junit.platform", "junit-platform-engine"), ("org.junit.platform", "junit-platform-commons"),
        ("org.junit.platform", "junit-platform-launcher"), ("org.opentest4j", "opentest4j"),
        ("org.apiguardian", "apiguardian-api"),
    ]
    result = []
    for group, artifact in coordinates:
        jars = sorted((cache / group / artifact).glob(f"*/*/{artifact}-*.jar"))
        if not jars:
            raise SystemExit(f"既存JUnit依存が必要です: {group}:{artifact}。DOGIDO_ACCEPTANCE_JUNIT_CPでも指定できます。")
        result.append(jars[-1])
    return result


def checked(result):
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    sys.stdout.flush()
    if result.returncode:
        raise SystemExit(result.returncode)


def wire_checks(golden):
    """見込み: 三状態と保存実画像の判定が符号化後も保持される。合格: 3合成状態と実pair_06のセル・4枚前参照・PNG hashが一致。範囲: Java→event JSON。受付世代は固定し、Rust受信・HTTP・発話は別確認。"""
    command = {"type": "capture_body_view", "command_id": "view_00000000-0000-0000-0000-000000000200",
        "session_id": "java-image-test", "issued_at": "2026-10-29T00:00:04Z", "expires_at": "2026-10-29T00:00:10Z",
        "body_entity_uuid": "00000000-0000-0000-0000-000000000001", "body_epoch": "00000000-0000-0000-0000-000000000002",
        "owner_uuid": "00000000-0000-0000-0000-000000000003", "dimension": "minecraft:overworld", "after_motion_sequence": 4}
    document = json.loads(golden.read_text(encoding="utf-8"))
    pair = next(row for row in document["cases"] if row["id"] == "pair_06")
    grey_hash = None
    for scenario, actual_images in [("unknown", False), ("unchanged", False), ("changed", False), ("changed", True)]:
        request = {"commands": [command], "event": {"fixture": "java_image_checks"},
            "captured_at": "2026-10-29T00:00:05Z", "scenario": scenario}
        if actual_images:
            request.update(before_path=str(golden.parent / pair["before"]), after_path=str(golden.parent / pair["after"]))
        result = run_fixture("BodyViewWireFixture", WIRE_PRODUCTION, input_bytes=json.dumps(request).encode())
        if result.returncode:
            checked(result)
        event = json.loads(result.stdout)
        assert len(event["command_results"]) == 1
        value = event["command_results"][0]
        assert value["status"] == "succeeded" and value["command_id"] == command["command_id"]
        view = value["body_view"]
        assert view["capture_id"] == command["command_id"] and view["motion_sequence"] == 5
        assert hashlib.sha256(base64.b64decode(view["png_base64"], validate=True)).hexdigest() == view["sha256"]
        assert not set(view).intersection({"visual_change", "compared_to_four_before", "pixel_mean_difference", "pixel_changed_fraction"})
        comparison = view["scene_comparison"]
        assert comparison["status"] == ("not_compared" if scenario == "unknown" else scenario)
        if scenario == "unknown":
            # Gsonはnullキーを省略し、Rust Optionは欠落もNoneとして読む。比較済み参照を補わないことを検査する。
            assert comparison.get("reference") is None and comparison["cells"] == []
            grey_hash = view["sha256"]
        else:
            ref = comparison["reference"]
            assert ref["capture_id"] == "view_00000000-0000-0000-0000-000000000100"
            assert ref["captured_at"] == "2026-10-29T00:00:01Z"
            if not actual_images:
                assert ref["sha256"] == grey_hash
            expected = [] if scenario == "unchanged" else [
                {"row": row, "column": column, "local_change": False, "brightness": "lighter"}
                for row in range(1, 7) for column in range(1, 9)]
            if actual_images:
                expected = [{"row": cell["row"], "column": cell["column"], "local_change": cell["local_change"],
                    "brightness": cell["brightness_direction"] if cell["brightness_change"] else "none"}
                    for cell in pair["expected"]["cells"] if cell["changed"]]
            assert comparison["cells"] == expected
        print(f"PASS: wire {scenario}{' pair_06 actual images' if actual_images else ' synthetic'}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["all", "unit", "golden", "wire"], default="all")
    parser.add_argument("--golden", type=Path, default=REPO / "docs/research/2026-10-29-image-change-handoff/golden-cells.json")
    args = parser.parse_args()
    production = ["screenshot/BodyViewChange.java", "screenshot/BodyViewBox.java"]
    if args.mode in {"all", "unit"}:
        checked(run_fixture("BodyViewJUnitRunner", production,
            extra_sources=[TEST_REPO / "adapter/minecraft-fabric/src/test/java/dogido/fabric/screenshot/BodyViewChangeTest.java"],
            extra_classpath=junit_classpath()))
    if args.mode in {"all", "golden"}:
        checked(run_fixture("BodyViewGoldenFixture", production, args=[args.golden]))
    if args.mode in {"all", "wire"}:
        if not __debug__:
            raise SystemExit("wireのassertを無効にするPython -Oは使用できません")
        wire_checks(args.golden)


if __name__ == "__main__":
    main()
