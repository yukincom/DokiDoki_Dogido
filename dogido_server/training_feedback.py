"""プレイ中の明示評価を、私的な学習候補箱へ保存する。

通常ログを教師データへ自動昇格させない。サーバーは直前の応答候補を
RAMにだけ保持し、プレイヤーが評価キーを押したときだけ匿名化済みsnapshotを
`.dogido_training/inbox/` へ追記する。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4


TRAINING_FEEDBACK_SCHEMA_VERSION = "dogido-training-feedback-v1"
TrainingFeedbackLabel = Literal["good_example", "needs_review"]


@dataclass(frozen=True, slots=True)
class TrainingEvaluationTarget:
    """評価キーが指す、直近1応答の匿名化済みRAM snapshot。"""

    target_id: str
    captured_at: datetime
    payload: dict[str, Any]


def anonymous_session_group(session_id: str) -> str:
    """ランダムsession IDを不可逆化し、split単位だけを残す。"""

    digest = sha256(session_id.encode("utf-8")).hexdigest()[:20]
    return f"session_{digest}"


def build_training_evaluation_target(
    *,
    target_id: str,
    captured_at: datetime,
    event: Any,
    actions: list[Any],
    state_mode: str,
    combat_active: bool,
    input_source: str,
    interpreted_player_text: str | None,
    player_input: Any,
    workshop: Any | None,
    conversation_history: list[str],
    event_digest: list[str],
    haiku_emission: Any | None,
    service_version: str,
    adapter_version: str,
    chat_model: str | None,
    haiku_model: str | None,
) -> TrainingEvaluationTarget | None:
    """直前応答と判断材料を、player識別子・座標フィールドなしで束ねる。"""

    rateable_actions = [
        action
        for action in actions
        if (action.text or "").strip() or action.cue_id or action.cue_sequence
    ]
    if not rateable_actions:
        return None

    raw_text = str(getattr(player_input, "raw_text", "") or "").strip()
    normalized_text = str(getattr(player_input, "normalized_text", "") or "").strip()
    semantic_text = str(getattr(player_input, "semantic_text", "") or "").strip()
    workshop_payload: dict[str, object] | None = None
    if workshop is not None and workshop.open:
        material_keys = (
            "biome",
            "biome_ja",
            "structure",
            "structure_ja",
            "held_item",
            "interpretation",
            "motifs",
            "generation_strategy",
            "prompt_variant",
            "haiku_constraints",
            "line_sources",
            "source_atoms",
        )
        workshop_payload = {
            "entry_id": workshop.entry_id,
            "current_surface": workshop.display_surface(),
            "current_reading": workshop.display_line(),
            "editing_surface": workshop.editing_surface(),
            "editing_reading": workshop.editing_line(),
            "pending_revision": workshop.pending_revision,
            "combat_paused": workshop.combat_paused,
            "materials": {
                key: workshop.materials[key]
                for key in material_keys
                if key in workshop.materials
            },
        }

    emission_payload: dict[str, object] | None = None
    if haiku_emission is not None:
        emission_payload = {
            "surface_text": haiku_emission.surface_text,
            "reading_text": haiku_emission.reading_text,
            "interpretation": haiku_emission.interpretation,
            "lines": [line.to_dict() for line in haiku_emission.lines],
            "route": haiku_emission.route,
        }

    if haiku_emission is not None:
        target_kind = "haiku"
    elif raw_text and workshop_payload is not None:
        target_kind = "haiku_workshop"
    elif raw_text:
        target_kind = "player_chat"
    else:
        target_kind = "ambient_or_safety"

    weather = getattr(event.world.weather, "value", event.world.weather)
    time_phase = getattr(event.world.time_phase, "value", event.world.time_phase)
    vehicle = event.player.vehicle.model_dump(mode="json") if event.player.vehicle else None
    look_target = None
    if event.look_target is not None:
        look_target = {"kind": event.look_target.kind, "name": event.look_target.name}
    payload = {
        "kind": target_kind,
        "event": {
            "sequence": event.sequence,
            "name": getattr(event.event.name, "value", event.event.name),
            "observed_at": event.observed_at.isoformat(),
        },
        "input": {
            "source": input_source if raw_text else None,
            "raw_text": raw_text or None,
            "normalized_text": normalized_text or None,
            "semantic_text": semantic_text or interpreted_player_text,
        },
        "output": {
            "actions": [
                {
                    "layer": action.layer,
                    "text": action.text,
                    "cue_id": action.cue_id,
                    "cue_sequence": list(action.cue_sequence),
                    "speech_profile": action.speech_profile,
                }
                for action in rateable_actions
            ]
        },
        "context": {
            "state_mode": state_mode,
            "combat_active": combat_active,
            "world": {
                "dimension": event.player.dimension,
                "biome": event.world.biome,
                "structure": event.world.structure,
                "time_phase": time_phase,
                "weather": weather,
            },
            "player": {
                "held_item": event.player.held_item,
                "vehicle": vehicle,
            },
            "observation": {
                "look_target": look_target,
                "visual_threat_types": [item.type for item in event.visual_threats[:8]],
                "auditory_threat_labels": [item.label for item in event.auditory_threats[:8]],
                "ambient_sound_types": [item.type for item in event.ambient_sounds[:8]],
                "passive_mob_types": [item.type for item in event.passive_mobs[:8]],
            },
            "conversation_history": conversation_history[-6:],
            "event_digest": event_digest[-6:],
        },
        "workshop": workshop_payload,
        "haiku": emission_payload,
        "runtime": {
            "service_version": service_version,
            "adapter_version": adapter_version,
            "adapter_build": event.meta.adapter_build,
            "chat_model": chat_model,
            "haiku_model": haiku_model,
        },
    }
    return TrainingEvaluationTarget(
        target_id=target_id,
        captured_at=captured_at,
        payload=payload,
    )


class TrainingFeedbackStore:
    """人が明示した評価だけを追記する、runtime memoryとは別の保存先。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.inbox_dir = root / "inbox"
        self.flags_path = self.inbox_dir / "evaluation_flags.jsonl"

    def append_flag(
        self,
        *,
        session_id: str,
        target: TrainingEvaluationTarget,
        label: TrainingFeedbackLabel,
        client_event_id: str,
        flagged_at: datetime,
        pressed_at: datetime | None,
        supersedes_flag_id: str | None,
    ) -> dict[str, Any]:
        flag_id = f"feedback_{uuid4().hex}"
        row = {
            "schema_version": TRAINING_FEEDBACK_SCHEMA_VERSION,
            "flag_id": flag_id,
            "target_id": target.target_id,
            "group_id": anonymous_session_group(session_id),
            "label": label,
            "flagged_at": flagged_at.isoformat(),
            "pressed_at": pressed_at.isoformat() if pressed_at is not None else None,
            "client_event_id": client_event_id,
            "supersedes_flag_id": supersedes_flag_id,
            "target": target.payload,
            # キー入力は強い人間signalだが、学習投入前の最終レビューは別に行う。
            "review": {"status": "unreviewed"},
        }
        self._append_private_jsonl(self.flags_path, row)
        return row

    def _append_private_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
            os.chmod(path.parent, 0o700)
        except OSError:
            # Windowsなどchmodの意味が異なる環境でもローカル保存は継続する。
            pass
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n"
            )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
