# state_machine/mixins/inventory.py
from __future__ import annotations

from datetime import datetime

from dogido_server.models import EventName, GameEvent, NearbyResource
from dogido_server.state_machine.constants import *  # noqa: F403
from dogido_server.state_machine.types import DerivedSignals

WEAPON_KEYWORDS = ("sword", "axe", "bow", "crossbow", "trident", "mace")
LIGHT_SOURCE_KEYS = ("torch", "soul_torch", "lantern", "soul_lantern")
TORCH_FUEL_KEYS = ("coal", "charcoal")
TORCH_FUEL_RESOURCE_NAMES = {"coal_ore", "coal", "charcoal"}


class InventoryMixin:
    def _can_emit_panic_cue(self, now: datetime) -> bool:
        if self.state.panic_scream_cooldown_until is None:
            return True
        return now >= self.state.panic_scream_cooldown_until

    def _has_torch(self, inventory: dict[str, int]) -> bool:
        return self._light_source_count(inventory) > 0

    def _has_weapon(self, event: GameEvent) -> bool:
        held_item = event.player.held_item or ""
        if any(keyword in held_item for keyword in WEAPON_KEYWORDS):
            return True
        return any(
            value > 0 and any(keyword in item_id for keyword in WEAPON_KEYWORDS)
            for item_id, value in event.inventory.items()
        )

    def _torch_craftable(self, inventory: dict[str, int]) -> bool:
        return self._inventory_count_for_keys(inventory, TORCH_FUEL_KEYS) >= 1 and inventory.get("stick", 0) >= 1

    def _light_source_count(self, inventory: dict[str, int]) -> int:
        return self._inventory_count_for_keys(inventory, LIGHT_SOURCE_KEYS)

    def _light_source_crafted(self, event: GameEvent) -> bool:
        if not self._is_status_snapshot(event) or not self.state.inventory_initialized:
            return False
        return self._light_source_count(event.inventory) > self.state.last_light_source_count

    def _entered_occluded_dark_zone(self, event: GameEvent) -> bool:
        return self._entered_snapshot_state(
            event,
            current=self._is_occluded_dark_zone_event(event),
            previous=self.state.last_occluded_dark_zone,
        )

    def _entered_safe_zone_with_door(self, event: GameEvent) -> bool:
        return self._entered_snapshot_state(
            event,
            current=self._is_safe_zone_with_door_event(event),
            previous=self.state.last_safe_zone_with_door,
        )

    def _exited_safe_zone_with_door(self, event: GameEvent) -> bool:
        if not self._is_status_snapshot(event):
            return False
        return (not self._is_safe_zone_with_door_event(event)) and bool(self.state.last_safe_zone_with_door)

    def _entered_submerged_dark_zone(self, event: GameEvent) -> bool:
        return self._entered_snapshot_state(
            event,
            current=self._is_submerged_dark_zone_event(event),
            previous=self.state.last_submerged_dark_zone,
        )

    def _entered_foliage_shade_context(self, event: GameEvent) -> bool:
        return self._entered_snapshot_state(
            event,
            current=self._is_foliage_shade_context(event),
            previous=self.state.last_foliage_shade_context,
        )

    def _should_warn_dark_push_no_light(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
    ) -> bool:
        del now
        if (
            self._dark_push_context_lost(signals)
            or self.state.dark_push_active
            or self.state.dark_push_stage < 1
            or not self._is_status_snapshot(event)
            or signals.entered_occluded_dark_zone
        ):
            return False
        return self._dark_push_progressed(event) and self._dark_push_is_scary_enough(event)

    def _should_continue_dark_push_breath(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
    ) -> bool:
        if (
            not self.state.dark_push_active
            or self._dark_push_context_lost(signals)
            or not self._is_status_snapshot(event)
        ):
            return False
        if self.state.dark_push_breath_ready_at is not None and now < self.state.dark_push_breath_ready_at:
            return False
        recent_ms = self._recent_ms(now, self.state.last_dark_push_breath_at)
        return recent_ms is None or recent_ms >= self.settings.dark_push_breath_loop_ms

    def _should_stop_dark_push_audio(self, event: GameEvent, signals: DerivedSignals) -> bool:
        if not self.state.dark_push_active:
            return False
        return self._dark_push_context_lost(signals) or self._dark_push_recovered(event)

    def _should_stop_dark_push_stage_one(self, event: GameEvent, signals: DerivedSignals) -> bool:
        if (
            self.state.dark_push_active
            or self.state.dark_push_stage < 1
            or not self._is_status_snapshot(event)
            or signals.entered_occluded_dark_zone
        ):
            return False
        return self._dark_push_context_lost(signals)

    def _reset_dark_push_state(self) -> None:
        self.state.pending_dark_push_after_breath_until = None
        self.state.dark_push_active = False
        self.state.dark_push_stage = 0
        self.state.dark_push_breath_ready_at = None
        self.state.dark_push_entry_x = None
        self.state.dark_push_entry_z = None

    def _set_dark_push_entry_reference(self, event: GameEvent) -> None:
        current_light = event.world.local_light
        current_darkness = event.world.danger_darkness_score
        if self._is_immediately_severe_dark_push_entry(event):
            self.state.dark_push_reference_light = (
                None
                if current_light is None
                else current_light + self.settings.dark_push_worse_light_delta
            )
            self.state.dark_push_reference_darkness = (
                None
                if current_darkness is None
                else max(0.0, current_darkness - self.settings.dark_push_worse_darkness_delta)
            )
        else:
            self.state.dark_push_reference_light = current_light
            self.state.dark_push_reference_darkness = current_darkness
        self.state.dark_push_entry_x = event.player.position.x
        self.state.dark_push_entry_z = event.player.position.z

    def _is_immediately_severe_dark_push_entry(self, event: GameEvent) -> bool:
        current_light = event.world.local_light
        current_darkness = event.world.danger_darkness_score
        if current_light is not None and current_light <= 3:
            return True
        if current_darkness is not None and current_darkness >= 0.9:
            return True
        return False

    def _dark_push_progressed(self, event: GameEvent) -> bool:
        if self._dark_push_moved_forward(event):
            return True
        if self.state.dark_push_entry_x is None or self.state.dark_push_entry_z is None:
            return self._dark_push_got_worse(event)
        return False

    def _dark_push_is_scary_enough(self, event: GameEvent) -> bool:
        current_light = event.world.local_light
        current_darkness = event.world.danger_darkness_score
        light_scary = (
            current_light is not None
            and current_light <= self.settings.dark_push_escalation_light_threshold
        )
        darkness_scary = (
            current_darkness is not None
            and current_darkness >= self.settings.dark_push_escalation_darkness_threshold
        )
        if current_light is not None and current_darkness is not None:
            return light_scary and darkness_scary
        return light_scary or darkness_scary

    def _dark_push_moved_forward(self, event: GameEvent) -> bool:
        entry_x = self.state.dark_push_entry_x
        entry_z = self.state.dark_push_entry_z
        current_x = event.player.position.x
        current_z = event.player.position.z
        if entry_x is None or entry_z is None or current_x is None or current_z is None:
            return False
        dx = current_x - entry_x
        dz = current_z - entry_z
        return (dx * dx + dz * dz) >= (self.settings.dark_push_progress_distance ** 2)

    def _dark_push_got_worse(self, event: GameEvent) -> bool:
        current_light = event.world.local_light
        current_darkness = event.world.danger_darkness_score
        reference_light = self.state.dark_push_reference_light
        reference_darkness = self.state.dark_push_reference_darkness
        if (
            current_light is not None
            and reference_light is not None
            and current_light <= reference_light - self.settings.dark_push_worse_light_delta
        ):
            return True
        if (
            current_darkness is not None
            and reference_darkness is not None
            and current_darkness >= reference_darkness + self.settings.dark_push_worse_darkness_delta
        ):
            return True
        return False

    def _dark_push_recovered(self, event: GameEvent) -> bool:
        current_light = event.world.local_light
        current_darkness = event.world.danger_darkness_score
        reference_light = self.state.dark_push_reference_light
        reference_darkness = self.state.dark_push_reference_darkness

        light_recovered = (
            current_light is not None
            and reference_light is not None
            and current_light >= reference_light
        )
        darkness_recovered = (
            current_darkness is not None
            and reference_darkness is not None
            and current_darkness <= reference_darkness + self.settings.dark_push_recover_darkness_margin
        )

        if reference_light is not None and reference_darkness is not None:
            return light_recovered or darkness_recovered
        if reference_light is not None:
            return light_recovered
        if reference_darkness is not None:
            return darkness_recovered
        return False

    def _has_bed(self, inventory: dict[str, int]) -> bool:
        return inventory.get("bed", 0) > 0 or self._inventory_has_suffix(inventory, "_bed")

    def _bed_craftable(self, inventory: dict[str, int]) -> bool:
        wool_count = self._wool_count(inventory)
        plank_count = self._plank_count(inventory)
        log_count = self._log_count(inventory)
        return wool_count >= 3 and (plank_count >= 3 or log_count >= 3)

    def _torch_near_craftable(self, inventory: dict[str, int], resources: list[NearbyResource]) -> bool:
        if self._torch_craftable(inventory):
            return True
        names = self._resource_names(resources)
        has_fuel = self._inventory_count_for_keys(inventory, TORCH_FUEL_KEYS) >= 1 or bool(
            TORCH_FUEL_RESOURCE_NAMES & names
        )
        has_wood = inventory.get("stick", 0) >= 1 or self._resource_names_have_wood(names)
        return has_fuel and has_wood

    def _bed_near_craftable(self, inventory: dict[str, int], resources: list[NearbyResource]) -> bool:
        if self._bed_craftable(inventory):
            return True
        names = self._resource_names(resources)
        has_wool = self._wool_count(inventory) >= 3 or self._resource_names_have_suffix(names, "_wool")
        has_wood = (
            self._plank_count(inventory) >= 3
            or self._log_count(inventory) >= 3
            or self._resource_names_have_wood(names)
        )
        return has_wool and has_wood

    def _has_high_cost_shelter_materials(self, inventory: dict[str, int]) -> bool:
        return self._inventory_count_for_keys(inventory, TORCH_FUEL_KEYS) > 0 or self._wool_count(inventory) > 0

    def _wool_count(self, inventory: dict[str, int]) -> int:
        return inventory.get("wool", 0) + self._inventory_count_with_suffix(inventory, "_wool")

    def _plank_count(self, inventory: dict[str, int]) -> int:
        return inventory.get("planks", 0) + self._inventory_count_with_suffix(inventory, "_planks")

    def _log_count(self, inventory: dict[str, int]) -> int:
        return inventory.get("oak_log", 0) + self._inventory_count_with_suffix(inventory, "_log")

    def _torch_materials_nearby(self, resources: list[NearbyResource]) -> bool:
        names = self._resource_names(resources)
        has_fuel = bool(TORCH_FUEL_RESOURCE_NAMES & names)
        has_wood = self._resource_names_have_wood(names)
        return has_fuel or has_wood

    def _bed_materials_nearby(self, resources: list[NearbyResource]) -> bool:
        names = self._resource_names(resources)
        has_wool = self._resource_names_have_suffix(names, "_wool")
        has_wood = self._resource_names_have_wood(names)
        return has_wool or has_wood

    def _is_status_snapshot(self, event: GameEvent) -> bool:
        return event.event.name == EventName.STATUS_SNAPSHOT

    def _entered_snapshot_state(
        self,
        event: GameEvent,
        *,
        current: bool,
        previous: bool | None,
    ) -> bool:
        return self._is_status_snapshot(event) and current and not bool(previous)

    def _dark_push_context_lost(self, signals: DerivedSignals) -> bool:
        return signals.submerged or signals.emergency_shelter or (not signals.occluded_dark_zone)

    def _inventory_count_for_keys(self, inventory: dict[str, int], keys: tuple[str, ...]) -> int:
        return sum(inventory.get(key, 0) for key in keys)

    def _inventory_count_with_suffix(self, inventory: dict[str, int], suffix: str) -> int:
        return sum(value for key, value in inventory.items() if key.endswith(suffix))

    def _inventory_has_suffix(self, inventory: dict[str, int], suffix: str) -> bool:
        return any(key.endswith(suffix) and value > 0 for key, value in inventory.items())

    def _resource_names(self, resources: list[NearbyResource]) -> set[str]:
        return {resource.name for resource in resources}

    def _resource_names_have_suffix(self, names: set[str], suffix: str) -> bool:
        return any(name.endswith(suffix) for name in names)

    def _resource_names_have_wood(self, names: set[str]) -> bool:
        return self._resource_names_have_suffix(names, "_log") or self._resource_names_have_suffix(names, "_planks")

    def _recent_ms(self, now: datetime, when: datetime | None) -> int | None:
        if when is None:
            return None
        return int((now - when).total_seconds() * 1000)

    def _older_than(self, value_ms: int | None, threshold_ms: int) -> bool:
        return value_ms is None or value_ms > threshold_ms

    def _prune_comment_memory(self, now: datetime) -> None:
        cooldown = self.settings.hostile_comment_cooldown_ms
        water_cooldown = self.settings.daylight_water_comment_cooldown_ms
        self.state.commented_visual_keys = {
            visual_key: commented_at
            for visual_key, commented_at in self.state.commented_visual_keys.items()
            if self._recent_ms(now, commented_at) is not None and self._recent_ms(now, commented_at) < cooldown
        }
        self.state.daylight_water_comment_keys = {
            visual_key: commented_at
            for visual_key, commented_at in self.state.daylight_water_comment_keys.items()
            if self._recent_ms(now, commented_at) is not None and self._recent_ms(now, commented_at) < water_cooldown
        }
        self.state.screamed_visual_keys = {
            visual_key: commented_at
            for visual_key, commented_at in self.state.screamed_visual_keys.items()
            if self._recent_ms(now, commented_at) is not None and self._recent_ms(now, commented_at) < cooldown
        }
        self.state.seen_visual_keys = {
            visual_key: seen_at
            for visual_key, seen_at in self.state.seen_visual_keys.items()
            if self._recent_ms(now, seen_at) is not None and self._recent_ms(now, seen_at) < cooldown
        }
        self.state.commented_auditory_keys = {
            key: value
            for key, value in self.state.commented_auditory_keys.items()
            if self._recent_ms(now, value[0]) is not None and self._recent_ms(now, value[0]) < cooldown
        }
        self.state.auditory_presence_states = {
            key: value
            for key, value in self.state.auditory_presence_states.items()
            if value.last_seen_at is not None
            and self._recent_ms(now, value.last_seen_at) is not None
            and self._recent_ms(now, value.last_seen_at) < cooldown
        }
