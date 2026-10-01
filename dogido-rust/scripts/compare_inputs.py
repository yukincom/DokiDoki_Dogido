#!/usr/bin/env python3
"""受信型・入力保持・重複判定を現行Pythonと比較。外部サービスもサーバーも起動しない。"""
from __future__ import annotations

import argparse
import ast
from collections import deque
from copy import deepcopy
from datetime import datetime
import json
import logging
from pathlib import Path
import random
import subprocess
import sys
from types import MethodType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from dogido_server.models import GameEvent, _SMELL_SOURCE_SHAPES


def event_cases():
    schema = GameEvent.model_json_schema()
    definitions = schema["$defs"]

    def resolve(spec):
        if "$ref" in spec:
            return definitions[spec["$ref"].rsplit("/", 1)[-1]]
        if "anyOf" in spec:
            return resolve(next(s for s in spec["anyOf"] if s.get("type") != "null"))
        return spec

    def sample(spec):
        spec = resolve(spec)
        title = spec.get("title")
        if title == "SmellObservation":
            return {"status": "none"}
        if title == "ZombieScentClue":
            return {"type": "zombie", "entity_id": "z1", "distance_band": "close", "certainty": "medium"}
        if "enum" in spec:
            return spec["enum"][0]
        if "const" in spec:
            return spec["const"]
        kind = spec["type"]
        if spec.get("format") == "date-time":
            return "2026-09-21T12:34:56.123456+09:00"
        if kind == "object":
            if "properties" in spec:
                return {k: sample(v) for k, v in spec["properties"].items()}
            return {"minecraft:torch": 4}
        if kind == "array":
            return [sample(spec["items"])]
        if kind == "string":
            return "試験"
        if kind == "boolean":
            return True
        return max(spec.get("minimum", 1), 1)

    full = sample(schema)
    GameEvent.model_validate(full)
    minimal = {key: full[key] for key in schema["required"]}
    cases = [("minimal_defaults", minimal), ("all_fields", full)]

    def change(name, path, value, *, remove=False):
        event = deepcopy(full)
        parent = event
        for key in path[:-1]:
            parent = parent[key]
        if remove:
            parent.pop(path[-1], None)
        else:
            parent[path[-1]] = value
        cases.append((name + ":" + ".".join(map(str, path)), event))

    def walk(spec, path=()):
        spec = resolve(spec)
        if spec.get("title") in {"SmellObservation", "ZombieScentClue"}:
            return  # 独立した意味検証ケースで比較する。
        for key, field in spec.get("properties", {}).items():
            current = path + (key,)
            value = resolve(field)
            change("null", current, None)
            change("missing", current, None, remove=True)
            for member in value.get("enum", []):
                change("enum:" + member, current, member)
            if "enum" in value or "const" in value:
                change("invalid_enum", current, "unknown_enum")
            if value.get("type") in {"integer", "number"}:
                for candidate in ["2", 2.0, 1.5, "bad", -1, True]:
                    change("numeric:" + str(candidate), current, candidate)
                if value.get("type") == "integer":
                    for candidate in ["1e2", "2_000", "2.00", "+2", " 2 ", "2.00000000000000001", "１２", "-0.0", "2."]:
                        change("integer_string:" + candidate, current, candidate)
                for limit in ("minimum", "maximum"):
                    if limit in value:
                        for delta in [-1, 0, 1]:
                            change(limit + str(delta), current, value[limit] + delta)
            if value.get("type") == "boolean":
                for candidate in ["off", "YES", 0, 1.0, 2, "maybe"]:
                    change("bool:" + str(candidate), current, candidate)
            for limit in ("minLength", "maxLength"):
                if limit in value:
                    for delta in [-1, 0, 1]:
                        change(limit + str(delta), current, "あ" * max(0, value[limit] + delta))
            if value.get("type") == "object" and "properties" in value:
                change("unknown_extra", current + ("future_field",), {"observation": [1, "あ"]})
                walk(value, current)
            if value.get("type") == "array":
                change("empty_array", current, [])
                change("invalid_item", current, [None])
                if "maxItems" in value:
                    change("too_many", current, [sample(value["items"])] * (value["maxItems"] + 1))
                if resolve(value["items"]).get("type") == "object":
                    walk(value["items"], current + (0,))
    walk(schema)
    cases.append(("top_level_extra", dict(minimal, future_field={"nested": [None, "猫"]})))
    for present in [False, True]:
        event = deepcopy(minimal)
        event["peaceful_mobs"] = [{"type": "cat", "future_field": 17}]
        if present:
            event["passive_mobs"] = [{"type": "goat"}]
        cases.append(("legacy_priority:" + str(present), event))
    change("hotbar_duplicate", ("player", "hotbar", "slots"), [{"slot": 0}, {"slot": 0}])
    for band in ["close", "mid", "far"]:
        for certainty in ["low", "medium", "high"]:
            change("legacy_scent", ("zombie_scent_clues",), [dict(type="zombie", entity_id="z", distance_band=band, certainty=certainty)])
    for identifier, (category, valence, kinds) in _SMELL_SOURCE_SHAPES.items():
        for source in ["entity", "block", "hotbar", "dropped_item", "biome", "mixed"]:
            for wrong in [False, True]:
                smell = dict(status="present", smell_id=identifier, category=category,
                             valence="mixed" if wrong else valence, source_kind=source,
                             specificity="source", effective_strength=1, rain_after_active=True)
                change("source_shape:" + identifier, ("smell_observation",), smell)
    for category in ["decay", "food", "flower", "mixed", "rain_after"]:
        for valence in ["pleasant", "unpleasant", "mixed"]:
            change("category_shape", ("smell_observation",), dict(status="present", smell_id=category,
                category=category, valence=valence, source_kind="mixed", specificity="category", effective_strength=1))
    for status in ["none", "present", "suppressed"]:
        for reason in [None, "rain", "snow", "thunder", "submerged", "unknown"]:
            change("status_shape", ("smell_observation",), dict(status=status, suppression_reason=reason))
    mixed = dict(status="present", smell_id="mixed", category="mixed", valence="mixed",
                 source_kind="mixed", specificity="mixed", effective_strength=12)
    for key, val in [(None, None), ("smell_id", "food"), ("effective_strength", 13),
                     ("temperature_modifier", -8), ("entity_id", "forbidden"), ("direction", {}),
                     ("distance", 2), ("count", 2)]:
        value = dict(mixed)
        if key:
            value[key] = val
        change("mixed_shape", ("smell_observation",), value)
    rain_after = dict(status="present", smell_id="rain_after", category="rain_after", valence="pleasant",
                      source_kind="block", specificity="source", effective_strength=1, rain_after_active=False)
    change("rain_after_inactive", ("smell_observation",), rain_after)
    for date in ["2026-09-21T00:00:00Z", "2026-09-21 00:00:00", "2026-09-21", 1_790_000_000,
                 1_790_000_000_000, "1790000000", "broken", True]:
        change("timestamp:" + str(date), ("observed_at",), date)
    # 観測なし/空の結果/明示noneを混ぜない。
    for body in [None, {}, [], "event"]:
        cases.append(("bad_top_level", body))
    return cases


def pure_reference_methods():
    """service全体をimport/起動せず、比較対象の純粋メソッドだけをASTで取り出す。"""
    tree = ast.parse((ROOT.parent / "dogido_server/service.py").read_text())
    selected = {"SessionInfo": {"is_stale_sequence", "remember_sequence", "remember_idempotency"},
                "DogidoService": {"_queue_player_input", "_promote_deferred_player_input"}}
    methods = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in selected:
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name in selected[node.name]:
                    method.decorator_list = []
                    methods.append(method)
    assert len(methods) == 5
    logger = logging.getLogger("input-comparison")
    logger.disabled = True
    scope = {"LOGGER": logger, "deque": deque}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *methods], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "service_pure_methods", "exec"), scope)
    return scope


def reference_trace(steps, methods):
    fields = ["text", "source", "display_text", "turn_id", "force_main_chat", "foreground_route"]
    session = SimpleNamespace(session_id="comparison", last_sequence=None, seen_sequences=deque(maxlen=2048), seen_sequence_set=set(),
                              seen_idempotency=deque(maxlen=2048), seen_idempotency_set=set(), deferred_player_inputs=deque(maxlen=8))
    for field in fields:
        setattr(session, "pending_player_" + field, False if field == "force_main_chat" else None)
    for name in ["remember_sequence", "remember_idempotency", "is_stale_sequence"]:
        setattr(session, name, MethodType(methods[name], session))

    def pending():
        if session.pending_player_text is None:
            return None
        return {f: getattr(session, "pending_player_" + f) or (False if f == "force_main_chat" else "") for f in fields}

    def clear():
        for field in fields:
            setattr(session, "pending_player_" + field, False if field == "force_main_chat" else None)

    output = []
    for step in steps:
        op = step["op"]
        result = None
        if op == "admit":
            seq, key = step.get("sequence"), step.get("key")
            admission = "new"
            if key and session.remember_idempotency(key):
                admission = "duplicate_key"
            elif seq is not None:
                if session.is_stale_sequence(seq):
                    admission = "stale_sequence"
                elif session.remember_sequence(seq):
                    admission = "duplicate_sequence"
            result = dict(admission=admission, last_sequence=session.last_sequence)
        elif op == "heartbeat":
            if step.get("sequence") is not None:
                session.last_sequence = step["sequence"]
            result = dict(last_sequence=session.last_sequence)
        elif op in {"enqueue", "replace"}:
            if op == "replace":
                clear()
            inputs = dict(step["input"])
            text = inputs.pop("text")
            inputs.setdefault("source", "text")
            result = dict(accepted=methods["_queue_player_input"](session, text, **inputs))
        elif op == "direct":
            text = step["text"].strip()
            if text:
                if session.pending_player_text == text:
                    clear()
                session.deferred_player_inputs = deque((v for v in session.deferred_player_inputs if v[0] != text), maxlen=8)
        elif op == "take":
            result = pending()
            clear()
        elif op == "promote":
            methods["_promote_deferred_player_input"](session)
        else:
            raise AssertionError(op)
        output.append(dict(result=result, queue=dict(pending=pending(),
            deferred=[dict(zip(fields, row)) for row in session.deferred_player_inputs])))
    return dict(trace=output)


def traces():
    sequence = [dict(op="admit", sequence=i) for i in range(2050)]
    sequence += [dict(op="admit", sequence=i) for i in [0, 2049, 2051]]
    sequence += [dict(op="admit", key="key"), dict(op="admit", key="key", sequence=9999),
                 dict(op="heartbeat", sequence=3000), dict(op="admit", sequence=2999),
                 dict(op="heartbeat", sequence=2), dict(op="admit", sequence=3)]
    sequence += [dict(op="admit", key=str(i)) for i in range(2050)]
    sequence += [dict(op="admit", key="key"), dict(op="admit", key="2049"), dict(op="admit")]
    queue = [dict(op="enqueue", input=dict(text="  剣って何？  ", source="voice", display_text="県って何？")),
             dict(op="enqueue", input=dict(text="剣って何？", source="text", force_main_chat=True, turn_id="あ" * 200))]
    queue += [dict(op="enqueue", input=dict(text=f"質問{i}")) for i in range(9)]
    queue += [dict(op="enqueue", input=dict(text="質問7", source="voice", force_main_chat=True)),
              dict(op="direct", text="質問3"), dict(op="direct", text="剣って何？"), dict(op="promote")]
    queue += [dict(op=op) for _ in range(10) for op in ["take", "promote"]]
    rng = random.Random(20260921)
    random_queue = []
    for _ in range(400):
        op = rng.choice(["enqueue", "enqueue", "replace", "take", "promote", "direct"])
        text = f"発言{rng.randrange(12)}"
        item = dict(op=op)
        if op in {"enqueue", "replace"}:
            item["input"] = dict(text=text, source=rng.choice(["text", "voice"]), force_main_chat=op == "enqueue" and rng.choice([True, False]))
        elif op == "direct":
            item["text"] = text
        random_queue.append(item)
    return [("sequence_eviction_and_heartbeat", sequence), ("queue_capacity_and_direct_collision", queue),
            ("queue_seeded_order", random_queue)]


def canonical(value):
    if isinstance(value, dict):
        return {key: (datetime.fromisoformat(v).isoformat() if key in {"observed_at", "executed_at"} and isinstance(v, str)
                      else canonical(v)) for key, v in value.items()}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/examples/check_inputs")
    args = parser.parse_args()
    cases, sequences = event_cases(), traces()
    requests = [dict(event=event) for _, event in cases] + [dict(steps=steps) for _, steps in sequences]
    result = subprocess.run([str(args.binary.resolve())], input="".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests),
                            text=True, capture_output=True, timeout=30, check=True)
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(responses) == len(requests)
    accepted = rejected = 0
    for (name, event), response in zip(cases, responses):
        try:
            expected = GameEvent.model_validate(event).model_dump(mode="json")
        except ValueError:
            assert not response["accepted"], f"Rust accepted invalid input: {name}"
            rejected += 1
            continue
        assert response["accepted"], f"Rust rejected {name}: {response.get('reason')}"
        assert canonical(response["event"]) == canonical(expected), f"normalized event mismatch: {name}"
        accepted += 1
    methods = pure_reference_methods()
    for (name, steps), response in zip(sequences, responses[len(cases):]):
        expected = reference_trace(steps, methods)
        if response != expected:
            for index, (actual, want) in enumerate(zip(response["trace"], expected["trace"])):
                assert actual == want, f"trace mismatch {name} step={index} {steps[index]}\n{actual}\n{want}"
            raise AssertionError(f"trace length mismatch: {name}")
    report = dict(event_cases=len(cases), accepted=accepted, rejected=rejected,
                  traces=[dict(name=name, steps=len(steps)) for name, steps in sequences],
                  model_calls=0, tts_calls=0, servers_started=0)
    output = ROOT / "reports/input-comparison.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
