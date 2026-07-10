# state_machine/types.py
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from math import inf

from dogido_server.memory_types import HaikuEmission
from dogido_server.models import VisualThreat


@dataclass(slots=True)
class AudioAction:
    layer: str
    interrupt: bool
    text: str | None = None
    cue_id: str | None = None
    protect_ms: int = 0


@dataclass(slots=True)
class AuditoryPresenceState:
    count: int = 0
    last_seen_at: datetime | None = None
    first_x: float | None = None
    first_z: float | None = None


@dataclass(slots=True)
class RuntimeState:
    mode: str = "normal"
    shut_up_count: int = 0
    last_non_silent_at: datetime | None = None
    last_player_input_at: datetime | None = None
    last_haiku_emitted_at: datetime | None = None
    pending_haiku_after_preface: bool = False
    # 友好・中立モブへの反応クールダウンは種ごとに管理する
    last_ambient_mob_comment_at_by_type: dict[str, datetime] = field(default_factory=dict)
    last_warden_sonic_boom_scream_at: datetime | None = None
    # 中立モブの敵対化検知: 平和な姿を最後に見た時刻と、警告の種別クールダウン
    recent_passive_mob_seen_at_by_type: dict[str, datetime] = field(default_factory=dict)
    last_neutral_turned_hostile_comment_at_by_type: dict[str, datetime] = field(default_factory=dict)
    last_time_phase: str | None = None
    suppression_started_at: datetime | None = None
    suppression_until: datetime | None = None
    aftermath_until: datetime | None = None
    last_visual_threat_at: datetime | None = None
    last_audio_threat_at: datetime | None = None
    last_damage_at: datetime | None = None
    low_health_warning_armed: bool = True
    last_combat_end_at: datetime | None = None
    last_darkness_advice_at: datetime | None = None
    last_foliage_darkness_advice_at: datetime | None = None
    last_submerged_darkness_advice_at: datetime | None = None
    panic_scream_cooldown_until: datetime | None = None
    last_occluded_hostile_presence_comment_at: datetime | None = None
    last_ambient_mob_comment_at: datetime | None = None
    last_damaging_light_warning_at: datetime | None = None
    last_magma_block_comment_at: datetime | None = None
    last_nearby_lightning_comment_at: datetime | None = None
    last_mining_fatigue_comment_at: datetime | None = None
    last_boss_omen_comment_at: datetime | None = None
    last_boss_omen_kind: str | None = None
    last_ominous_sound_seen_at: datetime | None = None
    last_ominous_sound_comment_at: datetime | None = None
    last_ominous_sound_kind: str | None = None
    last_ominous_sound_severity: int = 0
    ominous_sound_stage: int = 0
    last_warden_chasing_comment_at: datetime | None = None
    warden_attack_start_announced: bool = False
    warden_golem_army_announced: bool = False
    # クリスタル爆破・TNT装置・上空ちくちくの共通「そこまでして」ライン
    warden_extreme_tactic_announced: bool = False
    # エンダードラゴン戦
    last_dragon_seen_at: datetime | None = None
    dragon_perch_announced: bool = False
    last_dragon_approach_callout_at: datetime | None = None
    last_end_crystal_count: int | None = None
    pending_crystal_count_announce: int | None = None
    last_crystal_callout_at: datetime | None = None
    dragon_crystal_hint_announced: bool = False
    last_active_status_effects: set[str] = field(default_factory=set)
    last_confirmed_hostiles: list[str] = field(default_factory=list)
    last_known_hostile_directions: list[str] = field(default_factory=list)
    prior_recent_visual_ms: int | None = None
    prior_recent_audio_ms: int | None = None
    burning_visual_keys: set[str] = field(default_factory=set)
    last_occluded_dark_zone: bool | None = None
    last_light_source_count: int = 0
    inventory_initialized: bool = False
    last_inventory_counts: dict[str, int] = field(default_factory=dict)
    # player_chat 用の粗い出来事メモ（自然文）。service が DialogueContext に吸い上げる
    pending_dialogue_notes: list[str] = field(default_factory=list)
    # 撃破推定: 直近に見えていた敵対 entity_id -> type
    tracked_hostile_entities: dict[str, str] = field(default_factory=dict)
    recent_kill_counts: dict[str, int] = field(default_factory=dict)
    commented_visual_keys: dict[str, datetime] = field(default_factory=dict)
    commented_auditory_keys: dict[str, tuple[datetime, int]] = field(default_factory=dict)
    auditory_presence_states: dict[str, AuditoryPresenceState] = field(default_factory=dict)
    announced_hostile_counts: dict[str, int] = field(default_factory=dict)
    last_dark_push_comment_at: datetime | None = None
    last_dark_push_breath_at: datetime | None = None
    last_multi_hostile_report_at: datetime | None = None
    last_multi_hostile_count: int = 0
    last_mass_hostile_callout_at: datetime | None = None
    last_ground_hostile_count_within_query_range: int = 0
    mass_hostile_callout_latched: bool = False
    last_multi_species_report_at: datetime | None = None
    last_multi_species_signature: str = ""
    last_overwhelmed_report_at: datetime | None = None
    last_overwhelmed_signature: str = ""
    last_visual_priority_callout_at: datetime | None = None
    last_single_visual_type: str | None = None
    last_single_visual_at: datetime | None = None
    last_ushiro_call_at: datetime | None = None
    last_daylight_water_comment_at: datetime | None = None
    last_daylight_rain_comment_at: datetime | None = None
    last_burning_visual_comment_at: datetime | None = None
    dark_push_stage: int = 0
    dark_push_reference_light: int | None = None
    dark_push_reference_darkness: float | None = None
    dark_push_breath_ready_at: datetime | None = None
    dark_push_entry_x: float | None = None
    dark_push_entry_z: float | None = None
    pending_dark_push_after_breath_until: datetime | None = None
    daylight_water_comment_keys: dict[str, datetime] = field(default_factory=dict)
    screamed_visual_keys: dict[str, datetime] = field(default_factory=dict)
    seen_visual_keys: dict[str, datetime] = field(default_factory=dict)
    seen_boss_visual_keys: set[str] = field(default_factory=set)
    stalled_visual_signature: str = ""
    stalled_visual_started_at: datetime | None = None
    last_stalled_visual_comment_at: datetime | None = None
    dark_push_active: bool = False
    pending_safe_aftermath: bool = False
    last_safe_zone_with_door: bool | None = None
    last_emergency_shelter: bool | None = None
    last_submerged_dark_zone: bool | None = None
    last_foliage_shade_context: bool | None = None
    last_weather: str | None = None
    firefly_reacted_this_night: bool = False
    night_warning_pending: bool = False
    night_warning_emitted_this_cycle: bool = False
    # 注意喚起行を出した後、本文行の発話待ちであることを示す
    pending_night_warning_detail: bool = False
    pending_weather_transition_from: str | None = None
    pending_weather_transition_to: str | None = None
    last_sleep_prompt_at: datetime | None = None
    last_sleeping_neighbor_comment_at: datetime | None = None
    emergency_shelter_active: bool = False
    emergency_shelter_advised_this_cycle: bool = False
    emergency_shelter_seen_this_cycle: bool = False
    emergency_shelter_reset_ready: bool = False
    emergency_shelter_morning_announced: bool = False
    current_dimension: str | None = None
    last_dimension_change_at: datetime | None = None
    current_biome: str | None = None
    pending_overworld_return_line: bool = False
    pending_overworld_return_ready_at: datetime | None = None
    active_close_flying_visual_keys: set[str] = field(default_factory=set)
    pending_special_biome_line: str | None = None
    last_special_biome_comment_at: dict[str, datetime] = field(default_factory=dict)
    # プレイヤー座標を含む構造物（村・要塞など）。構造物内ではバイオーム入場コメントを抑制する
    current_structure: str | None = None
    pending_structure_entry_key: str | None = None
    last_structure_comment_at: dict[str, datetime] = field(default_factory=dict)
    last_ender_eye_comment_at: datetime | None = None
    reacted_portal_types: set[str] = field(default_factory=set)
    pending_portal_type: str | None = None
    portal_state_initialized: bool = False
    last_portal_frame_comment_at: datetime | None = None
    last_haiku_block_log_at: datetime | None = None


@dataclass(slots=True)
class DerivedSignals:
    dimension_changed: bool = False
    nearest_visual_threat_distance: float = inf
    visual_threat_count_within_7: int = 0
    visual_threat_count_within_10: int = 0
    ground_hostile_count_within_query_range: int = 0
    flying_hostile_count_within_query_range: int = 0
    has_approaching_visual_threat: bool = False
    recent_hostile_audio_ms: int | None = None
    recent_hostile_visual_ms: int | None = None
    recent_damage_ms: int | None = None
    combat_active_hint: bool = False
    combat_end_candidate: bool = False
    danger_darkness_score: float = 0.0
    torch_available: bool = False
    torch_craftable: bool = False
    bed_available: bool = False
    bed_craftable: bool = False
    torch_near_craftable: bool = False
    bed_near_craftable: bool = False
    torch_materials_nearby: bool = False
    bed_materials_nearby: bool = False
    high_cost_material_owned: bool = False
    home_or_respawn_return_is_unrealistic: bool = False
    rear_high_risk: bool = False
    newly_burning_visual: VisualThreat | None = None
    entered_close_flying_visual: VisualThreat | None = None
    occluded_dark_zone: bool = False
    entered_occluded_dark_zone: bool = False
    light_source_crafted: bool = False
    submerged: bool = False
    emergency_shelter: bool = False
    entered_emergency_shelter: bool = False
    safe_zone_with_door: bool = False
    entered_safe_zone_with_door: bool = False
    exited_safe_zone_with_door: bool = False
    submerged_dark_zone: bool = False
    entered_submerged_dark_zone: bool = False
    weather_transition_from: str | None = None
    weather_transition_to: str | None = None
    cold_weather_biome: bool = False
    dry_weather_biome: bool = False
    entered_mining_fatigue: bool = False


@dataclass(slots=True)
class StateMachineResult:
    state: RuntimeState
    combat_active: bool
    actions: list[AudioAction]
    haiku_emission: HaikuEmission | None = None

    def as_actions(self) -> list[dict[str, str | bool | None]]:
        return [asdict(action) for action in self.actions]
