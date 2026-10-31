"""HTTP模擬試験の身体観測を作る。実adapterの取得や本体の補完処理には使わない。

環境の既存fixtureを、明示的に同じ場所へ置いた身体の感覚にも使う。
二者が異なる試験では senses に独立した値を渡し、playerの所有状態は複製しない。
"""
from copy import deepcopy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OWNER_ID = "11111111-1111-4111-8111-111111111111"
SENSE_FIELDS = (
    "world", "environment", "passive_mobs", "visual_threats", "nearby_resources", "dropped_items",
    "nearby_composition", "smell_observation", "auditory_threats", "ambient_sounds",
)


def attach_body(event, *, senses=None, body_position=None, visibility="visible", age_ms=0, activity=None):
    """一つの模擬eventへv3観測を加える。元のplayer/戦闘/指差しは書き換えない。"""
    event = deepcopy(event)
    body = json.loads((ROOT / "fixtures/body-observation-v3.json").read_text())
    sample = body["sample"]
    tick = event["sequence"]
    sample["server_tick"] = tick
    sample["snapshot_revision"] = tick
    sample["context"]["dimension"] = event.get("player", {}).get("dimension", "minecraft:overworld")
    event.setdefault("player", {}).setdefault("dimension", sample["context"]["dimension"])
    origin = body_position or event["player"].get("position") or {"x": 10, "y": 64, "z": 20}
    origin = {key: origin.get(key, default) for key, default in (("x", 10), ("y", 64), ("z", 20))}
    if event["player"].get("position"):
        sample["player_pose"]["position"] = {key: event["player"]["position"].get(key, origin[key]) for key in origin}
    sample["self_pose"]["position"] = origin
    sample["self_pose"]["eye_position"] = {**origin, "y": origin["y"] + 1}
    sample["vision"].update(player_visibility=visibility,
                            reason="in_view" if visibility == "visible" else "outside_fov")
    fields = event if senses is None else senses
    defaults = {field: [] for field in SENSE_FIELDS}
    defaults.update(world={}, environment=None, nearby_composition={"mobs": [], "blocks": [],
                    "mobs_truncated": False, "blocks_truncated": False},
                    smell_observation={"status": "none", "components": [], "basis": "smell_policy_v2"})
    for field in SENSE_FIELDS:
        sample["senses"][field] = deepcopy(fields.get(field, defaults[field]))
    for field in ("air_supply", "respawn_point_set", "respawn_distance"):
        sample["senses"]["world"].pop(field, None)
    for field in ("auditory_threats", "ambient_sounds"):
        for sound in sample["senses"][field]:
            sound.setdefault("heard_ago_ms", 0)
    sample["senses"]["source_server_tick"] = tick
    body["age_ms"] = age_ms
    event["body_observation"] = body
    if activity is not None:
        # 熱源等の試験で必要なときだけ、同じframeの実活動を明示する。
        # HOLDなので自律モデルの見物は開始せず、停止・移動の事実だけを検査できる。
        event["body_autonomy"] = {"version": 2, "status": "observed", "context": deepcopy(sample["context"]),
            "server_tick": tick, "snapshot_revision": tick, "age_ms": age_ms,
            "enabled": True, "mode": "hold", "follow_distance": 16, "warp_distance": 24,
            "activity": activity, "reason": "fixture_activity",
            "reassurance": {"value": 100, "owner_distance": 2, "owner_visible": True,
                            "face_visible": True, "owner_working": False},
            "attention": {"scope": "ambient", "expansion": 1}, "candidates": []}
    return event
