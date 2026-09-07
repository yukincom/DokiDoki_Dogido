from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from dogido_server.models import (
    AdapterCommandResult,
    GameEvent,
    OutputFlags,
    SelectHotbarCommand,
)

if TYPE_CHECKING:
    from dogido_server.state_machine.types import AudioAction


LOGGER = logging.getLogger("uvicorn.error")

EPISODE_SCHEMA_VERSION = 3
EPISODE_RELATIVE_PATH = Path("eval") / "episodes.jsonl"


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _direction_payload(direction: object) -> dict[str, object]:
    return {
        "horizontal": _enum_value(getattr(direction, "horizontal", None)),
        "cardinal": _enum_value(getattr(direction, "cardinal", None)),
        "vertical": _enum_value(getattr(direction, "vertical", None)),
    }


def _visual_threat_payload(threat: object) -> dict[str, object]:
    return {
        "type": getattr(threat, "type", None),
        "entity_id": getattr(threat, "entity_id", None),
        "distance": getattr(threat, "distance", None),
        "direction": _direction_payload(getattr(threat, "direction", None)),
        "approaching": getattr(threat, "approaching", False),
        "fuse_active": getattr(threat, "fuse_active", None),
        "on_fire": getattr(threat, "on_fire", False),
        "in_water": getattr(threat, "in_water", False),
        "certainty": _enum_value(getattr(threat, "certainty", None)),
    }


def _auditory_threat_payload(threat: object) -> dict[str, object]:
    return {
        "label": getattr(threat, "label", None),
        "source_id": getattr(threat, "source_id", None),
        "sound_event": getattr(threat, "sound_event", None),
        "direction": _direction_payload(getattr(threat, "direction", None)),
        "distance_band": _enum_value(getattr(threat, "distance_band", None)),
        "certainty": _enum_value(getattr(threat, "certainty", None)),
        "spoken_name_allowed": getattr(threat, "spoken_name_allowed", False),
    }


def _observation_payload(event: GameEvent) -> dict[str, object]:
    player = event.player
    world = event.world
    combat = event.combat
    visual_items = [_visual_threat_payload(threat) for threat in event.visual_threats]
    auditory_items = [_auditory_threat_payload(threat) for threat in event.auditory_threats]
    nearest_visual = min(
        visual_items,
        key=lambda threat: (
            threat["distance"] if threat["distance"] is not None else float("inf")
        ),
        default=None,
    )
    return {
        "player": {
            "dimension": player.dimension,
            "position": {
                "x": player.position.x,
                "y": player.position.y,
                "z": player.position.z,
            },
            "health": player.health,
            "hunger": player.hunger,
            "held_item": player.held_item,
            "block_breaking_active": player.block_breaking_active,
            "yaw": player.yaw,
            "pitch": player.pitch,
            "hotbar": (
                player.hotbar.model_dump(mode="json")
                if player.hotbar is not None
                else None
            ),
            "vehicle": (
                player.vehicle.model_dump(mode="json")
                if player.vehicle is not None
                else None
            ),
            "active_status_effects": list(player.active_status_effects),
        },
        "world": {
            "biome": world.biome,
            "structure": world.structure,
            "time_of_day": world.time_of_day,
            "time_phase": _enum_value(world.time_phase),
            "weather": _enum_value(world.weather),
            "local_light": world.local_light,
            "sky_visible": world.sky_visible,
            "surface_y": world.surface_y,
            "depth_below_surface": world.depth_below_surface,
            "ceiling_height": world.ceiling_height,
            "overhead_cover_type": world.overhead_cover_type,
            "is_submerged": world.is_submerged,
            "submerged_depth_blocks": world.submerged_depth_blocks,
            "air_supply": world.air_supply,
            "cardinal_wall_count": world.cardinal_wall_count,
            "double_height_open_side_count": world.double_height_open_side_count,
            "drafty_opening_count": world.drafty_opening_count,
            "enclosure_score": world.enclosure_score,
            "connected_dark_volume": world.connected_dark_volume,
            "nearest_dark_spawn_distance": world.nearest_dark_spawn_distance,
            "danger_darkness_score": world.danger_darkness_score,
            "nearby_light_source_count": world.nearby_light_source_count,
            "nearest_light_source_distance": world.nearest_light_source_distance,
            "nearby_door_count": world.nearby_door_count,
            "open_door_count": world.open_door_count,
            "nearby_window_present": world.nearby_window_present,
            "nearby_bed_count": world.nearby_bed_count,
            "nearby_sleeping_people_count": world.nearby_sleeping_people_count,
            "safe_zone_with_door": world.safe_zone_with_door,
            "respawn_point_set": world.respawn_point_set,
            "respawn_distance": world.respawn_distance,
        },
        "visual_threats": {
            "count": len(visual_items),
            "types": sorted({threat.type for threat in event.visual_threats}),
            "nearest": nearest_visual,
            "items": visual_items,
        },
        "auditory_threats": {
            "count": len(auditory_items),
            "labels": sorted({threat.label for threat in event.auditory_threats}),
            "distance_bands": sorted(
                {
                    str(_enum_value(threat.distance_band))
                    for threat in event.auditory_threats
                    if threat.distance_band is not None
                }
            ),
            "items": auditory_items,
        },
        "ambient_sounds": [
            {
                "type": sound.type,
                "source_id": sound.source_id,
                "sound_event": sound.sound_event,
                "direction": _direction_payload(sound.direction),
                "distance_band": _enum_value(sound.distance_band),
                "certainty": _enum_value(sound.certainty),
            }
            for sound in event.ambient_sounds
        ],
        "passive_mobs": {
            "count": len(event.passive_mobs),
            "types": sorted({mob.type for mob in event.passive_mobs}),
            "items": [
                {
                    "type": mob.type,
                    "distance": mob.distance,
                    "direction": _direction_payload(mob.direction),
                    "certainty": _enum_value(mob.certainty),
                    "temperament": mob.temperament,
                    "caution_reason": mob.caution_reason,
                    "is_baby": mob.is_baby,
                    "profession": mob.profession,
                    "villager_type": mob.villager_type,
                }
                for mob in event.passive_mobs
            ],
        },
        "inventory": dict(event.inventory),
        "nearby_resources": [
            {
                "type": resource.type,
                "name": resource.name,
                "distance": resource.distance,
                "direction": _direction_payload(resource.direction),
            }
            for resource in event.nearby_resources
        ],
        "dropped_items": [
            item.model_dump(mode="json") for item in event.dropped_items
        ],
        "recent_block_breaks": [
            broken.model_dump(mode="json") for broken in event.recent_block_breaks
        ],
        "look_target": (
            {
                "kind": event.look_target.kind,
                "name": event.look_target.name,
                "distance": event.look_target.distance,
            }
            if event.look_target is not None
            else None
        ),
        "combat": {
            "combat_active_hint": combat.combat_active_hint,
            "recent_damage_ms": combat.recent_damage_ms,
            "recent_hostile_visual_ms": combat.recent_hostile_visual_ms,
            "recent_hostile_audio_ms": combat.recent_hostile_audio_ms,
            "hostiles_within_7": combat.hostiles_within_7,
            "hostiles_within_10": combat.hostiles_within_10,
            "hostile_scan_distance": combat.hostile_scan_distance,
            "hostiles_within_scan_ground": combat.hostiles_within_scan_ground,
            "hostiles_within_30_ground": combat.hostiles_within_30_ground,
            "hostile_outcomes": [
                outcome.model_dump(mode="json") for outcome in (combat.hostile_outcomes or [])
            ]
            if combat.hostile_outcomes is not None
            else None,
        },
    }


def _action_payload(action: AudioAction) -> dict[str, object]:
    return {
        "kind": "audio",
        "layer": action.layer,
        "text": action.text,
        "cue_id": action.cue_id,
        "cue_sequence": list(action.cue_sequence),
        "interrupt": action.interrupt,
        "protect_ms": action.protect_ms,
        "speech_profile": action.speech_profile,
        "speed_scale": action.speed_scale,
        "speech_segments": list(action.speech_segments),
        "speech_segment_pause_ms": action.speech_segment_pause_ms,
        "queue_priority": action.queue_priority,
        "queue_replace_key": action.queue_replace_key,
        "references": [
            {
                "source_id": reference.source_id,
                "title_ja": reference.title_ja,
                "citation_label_ja": reference.citation_label_ja,
                "locator": reference.locator,
                "url": reference.url,
                "source_kind": reference.source_kind,
            }
            for reference in action.references
        ],
    }


class EpisodeRecorder:
    """Append-only decision records, separate from conversational memory.

    The service is the single writer in normal operation. The lock also keeps
    direct/test callers from interleaving JSON lines. Recording is best-effort:
    no filesystem or serialization failure may stop real-time event handling.
    """

    def __init__(self, root: Path) -> None:
        self.path = root / EPISODE_RELATIVE_PATH
        self._lock = Lock()

    def record(
        self,
        *,
        event: GameEvent,
        event_id: str,
        session_id: str,
        state_before: dict[str, object],
        mode_after: str,
        combat_active: bool,
        actions: list[AudioAction],
        output_flags: OutputFlags,
        haiku_emitted: bool,
        interpreted_user_text: str | None,
        recorded_at: datetime,
        adapter_commands: list[SelectHotbarCommand] | None = None,
        command_results: list[AdapterCommandResult] | None = None,
    ) -> bool:
        action_items = [_action_payload(action) for action in actions]
        adapter_command_items = [
            command.model_dump(mode="json") for command in (adapter_commands or [])
        ]
        command_result_items = [
            result.model_dump(mode="json") for result in (command_results or [])
        ]
        raw_user_text = (event.meta.user_text or "").strip() or None
        interpreted = (interpreted_user_text or "").strip() or None
        payload: dict[str, Any] = {
            "schema_version": EPISODE_SCHEMA_VERSION,
            "record_type": "decision_episode",
            "episode_id": f"ep_{uuid4().hex}",
            "recorded_at": recorded_at.isoformat(),
            "session_id": session_id,
            "event_id": event_id,
            "trigger": {
                "input_schema_version": event.schema_version,
                "game": event.game,
                "adapter": event.adapter,
                "event_name": _enum_value(event.event.name),
                "source_kind": _enum_value(event.event.source_kind),
                "priority_hint": _enum_value(event.event.priority_hint),
                "certainty": _enum_value(event.event.certainty),
                "sequence": event.sequence,
                "observed_at": event.observed_at.isoformat(),
                "player_input": (
                    {
                        "raw": raw_user_text,
                        "interpreted": interpreted if interpreted != raw_user_text else None,
                    }
                    if raw_user_text is not None or interpreted is not None
                    else None
                ),
            },
            "observation": _observation_payload(event),
            "state_before": state_before,
            "decision": {
                "source": "state_machine_and_service_policy",
                "kind": (
                    "emit_actions"
                    if action_items or adapter_command_items
                    else "observe_adapter_result"
                    if command_result_items
                    else "no_action"
                ),
                "mode_after": mode_after,
                "mode_changed": state_before.get("mode") != mode_after,
                "combat_active": combat_active,
                "haiku_emitted": haiku_emitted,
                "layers": [str(item["layer"]) for item in action_items],
            },
            "action": {
                "count": len(action_items),
                "items": action_items,
                "adapter_commands": adapter_command_items,
            },
            "result": {
                "status": (
                    "actions_selected"
                    if action_items
                    else "commands_selected"
                    if adapter_command_items
                    else "adapter_result_observed"
                    if command_result_items
                    else "no_action"
                ),
                "scope": (
                    "adapter_execution_observed"
                    if command_result_items
                    else "service_decision"
                ),
                "output_flags": output_flags.model_dump(mode="json"),
                "adapter_command_results": command_result_items,
            },
        }
        return self._append(payload)

    def _append(self, payload: dict[str, Any]) -> bool:
        try:
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
        except Exception as exc:  # best-effort boundary: real-time processing must continue
            LOGGER.warning("episode_write_failed path=%s detail=%s", self.path, exc)
            return False
        return True
