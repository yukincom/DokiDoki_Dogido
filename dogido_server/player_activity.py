"""プレイヤーの乗車状態を、LLMへ渡す主語付きの観測事実へ変換する。"""

from __future__ import annotations

from dogido_server.entry_catalog import item_entry, mob_entry
from dogido_server.models import VehicleState


# カタログ未収録、またはアイテムIDとエンティティIDが異なる乗り物だけ補う。
_VEHICLE_LABEL_FALLBACKS = {
    "boat": "ボート",
    "chest_boat": "チェスト付きボート",
    "raft": "イカダ",
    "chest_raft": "チェスト付きイカダ",
    "minecart": "トロッコ",
    "chest_minecart": "チェスト付きトロッコ",
    "command_block_minecart": "コマンドブロック付きトロッコ",
    "furnace_minecart": "かまど付きトロッコ",
    "hopper_minecart": "ホッパー付きトロッコ",
    "spawner_minecart": "スポナー付きトロッコ",
    "tnt_minecart": "TNT付きトロッコ",
    "donkey": "ロバ",
    "nautilus": "ノーチラス",
    "zombie_nautilus": "ゾンビノーチラス",
}

_ACTIVITY_PHRASES = {
    "riding": "乗っている",
    "moving": "乗って移動している",
    "running": "乗って走っている",
    "rowing": "乗って漕いでいる",
    "dashing": "乗ってダッシュしている",
}


def vehicle_label(vehicle_id: str) -> str:
    """保存済みカタログを優先し、乗り物の日本語名を返す。"""

    normalized = str(vehicle_id or "").strip().lower().removeprefix("minecraft:")
    if not normalized:
        return "乗り物"
    for entry in (mob_entry(normalized), item_entry(normalized)):
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or entry.get("japanese") or "").strip()
        if label:
            return label
    # 未知のmod乗り物IDを英字のままLLMへ漏らさず、意味だけを保守的に伝える。
    return _VEHICLE_LABEL_FALLBACKS.get(normalized, "乗り物")


def player_vehicle_fact(vehicle: VehicleState | None) -> str:
    """乗車中だけ、ドギド自身と取り違えない主語付きの一文を返す。"""

    if vehicle is None:
        return ""
    label = vehicle_label(vehicle.vehicle_id)
    activity = _ACTIVITY_PHRASES.get(str(vehicle.activity), "乗っている")
    return f"プレイヤーは{label}に{activity}"
