from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DogidoModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SourceKind(str, Enum):
    VISUAL = "visual"
    AUDITORY = "auditory"
    INFERRED = "inferred"
    SYSTEM = "system"


class Certainty(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PriorityHint(str, Enum):
    CRITICAL = "critical"
    URGENT = "urgent"
    NORMAL = "normal"
    BACKGROUND = "background"


class HorizontalDirection(str, Enum):
    FRONT = "front"
    FRONT_RIGHT = "front_right"
    RIGHT = "right"
    BACK_RIGHT = "back_right"
    BACK = "back"
    BACK_LEFT = "back_left"
    LEFT = "left"
    FRONT_LEFT = "front_left"


class VerticalRelation(str, Enum):
    ABOVE = "above"
    SAME = "same"
    BELOW = "below"


class DistanceBand(str, Enum):
    TOUCHING = "touching"
    VERY_CLOSE = "very_close"
    CLOSE = "close"
    MID = "mid"
    FAR = "far"


class EventName(str, Enum):
    THREAT_DETECTED = "threat_detected"
    THREAT_APPROACHING = "threat_approaching"
    HOSTILE_AUDIO_DETECTED = "hostile_audio_detected"
    DANGER_DARKNESS_CHANGED = "danger_darkness_changed"
    RESOURCE_OPTION_FOUND = "resource_option_found"
    AMBIENT_MOB_DETECTED = "ambient_mob_detected"
    PLAYER_DIED = "player_died"
    TIME_PHASE_CHANGED = "time_phase_changed"
    COMBAT_ENDED = "combat_ended"
    STATUS_SNAPSHOT = "status_snapshot"


class TimePhase(str, Enum):
    MORNING = "morning"
    DAY = "day"
    EVENING = "evening"
    NIGHT = "night"


class Weather(str, Enum):
    CLEAR = "clear"
    RAIN = "rain"
    THUNDER = "thunder"


class Direction(DogidoModel):
    horizontal: HorizontalDirection | None = None
    vertical: VerticalRelation | None = None


class Position(DogidoModel):
    x: float | None = None
    y: float | None = None
    z: float | None = None


class EventDescriptor(DogidoModel):
    name: EventName
    source_kind: SourceKind
    priority_hint: PriorityHint
    certainty: Certainty


class PlayerState(DogidoModel):
    name: str | None = None
    position: Position = Field(default_factory=Position)
    yaw: float | None = None
    pitch: float | None = None
    health: float | None = None
    hunger: int | None = None
    dimension: str | None = None
    held_item: str | None = None


class WorldState(DogidoModel):
    time_of_day: int | None = None
    time_phase: TimePhase | None = None
    weather: Weather | None = None
    biome: str | None = None
    local_light: int | None = None
    sky_visible: bool | None = None
    ceiling_height: float | None = None
    overhead_cover_type: str | None = None
    enclosure_score: float | None = None
    connected_dark_volume: int | None = None
    nearest_dark_spawn_distance: float | None = None
    danger_darkness_score: float | None = None


class VisualThreat(DogidoModel):
    type: str
    entity_id: str | None = None
    distance: float | None = None
    direction: Direction = Field(default_factory=Direction)
    approaching: bool = False
    on_fire: bool = False
    certainty: Certainty = Certainty.HIGH


class AuditoryThreat(DogidoModel):
    label: str
    sound_event: str | None = None
    direction: Direction = Field(default_factory=Direction)
    distance_band: DistanceBand | None = None
    certainty: Certainty = Certainty.LOW
    spoken_name_allowed: bool = False


class PeacefulMob(DogidoModel):
    type: str
    distance: float | None = None
    direction: Direction = Field(default_factory=Direction)
    certainty: Certainty = Certainty.HIGH


class NearbyResource(DogidoModel):
    type: str
    name: str
    distance: float | None = None
    direction: Direction = Field(default_factory=Direction)


class CombatState(DogidoModel):
    recent_damage_ms: int | None = Field(default=None, ge=0)
    recent_hostile_visual_ms: int | None = Field(default=None, ge=0)
    recent_hostile_audio_ms: int | None = Field(default=None, ge=0)
    hostiles_within_7: int | None = Field(default=None, ge=0)
    hostiles_within_10: int | None = Field(default=None, ge=0)
    combat_active_hint: bool | None = None


class MetaState(DogidoModel):
    adapter_build: str | None = None
    profile_name: str | None = None
    debug: bool = False
    death_cause: str | None = None
    user_text: str | None = None


class GameEvent(DogidoModel):
    schema_version: str
    game: str = "minecraft-java"
    adapter: str
    observed_at: datetime
    sequence: int | None = Field(default=None, ge=0)
    event: EventDescriptor
    player: PlayerState = Field(default_factory=PlayerState)
    world: WorldState = Field(default_factory=WorldState)
    visual_threats: list[VisualThreat] = Field(default_factory=list)
    auditory_threats: list[AuditoryThreat] = Field(default_factory=list)
    peaceful_mobs: list[PeacefulMob] = Field(default_factory=list)
    inventory: dict[str, int] = Field(default_factory=dict)
    nearby_resources: list[NearbyResource] = Field(default_factory=list)
    combat: CombatState = Field(default_factory=CombatState)
    meta: MetaState = Field(default_factory=MetaState)


class AdapterSessionCreateRequest(DogidoModel):
    adapter_name: str
    adapter_version: str
    game: str = "minecraft-java"
    schema_version: str
    player_name: str
    profile_name: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class AdapterSessionCreateResponse(DogidoModel):
    session_id: str
    accepted_schema_version: str
    server_time: datetime
    event_endpoint: str
    batch_endpoint: str
    heartbeat_interval_ms: int
    max_batch_size: int


class BatchEventRequest(DogidoModel):
    events: list[GameEvent] = Field(default_factory=list)


class HeartbeatRequest(DogidoModel):
    last_sequence: int | None = Field(default=None, ge=0)
    sent_at: datetime


class HealthResponse(DogidoModel):
    ok: bool
    service: str
    version: str


class StateResponse(DogidoModel):
    mode: str
    combat_active: bool


class OutputFlags(DogidoModel):
    panic_cue_enqueued: bool = False
    callout_enqueued: bool = False
    speech_enqueued: bool = False


class AcceptedEventResponse(DogidoModel):
    accepted: bool
    event_id: str
    session_id: str
    sequence: int | None = None
    deduplicated: bool = False
    state: StateResponse | None = None
    outputs: OutputFlags | None = None
    server_time: datetime


class BatchAcceptedResponse(DogidoModel):
    accepted: bool
    received: int
    processed: int
    deduplicated: int
    server_time: datetime


class HeartbeatResponse(DogidoModel):
    ok: bool
    session_id: str
    server_time: datetime


class CloseSessionResponse(DogidoModel):
    ok: bool
    session_id: str


class StateSummary(DogidoModel):
    mode: str
    combat_active: bool
    actions: list[dict[str, Any]] = Field(default_factory=list)
