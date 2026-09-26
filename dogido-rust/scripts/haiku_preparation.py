"""One-job, in-memory preparation/projection for the native haiku engine.

The caller owns scheduling, inference, playback, acceptance, and persistence.
This module neither drives the state machine nor creates a model frontend. It
keeps the first observation and its randomly chosen material for the whole job.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from pydantic import Field

from dogido_server.config import Settings
from dogido_server.dialogue.foreground import ForegroundDialogue
from dogido_server.haiku.source_atoms import merge_source_atoms
from dogido_server.llm.types import StructuredGenerationRequest
from dogido_server.models import GameEvent
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.haiku_context import IronyContext, SceneContext


class _PreparationSettings(Settings):
    # Older Python snapshots predate this separate inspection budget. Keep its
    # current contract here while native Rust already owns the inspection loop.
    haiku_grounding_max_tokens: int = Field(default=512, ge=1)


def compose_inspiration_speech(*, found: bool, description: str) -> str:
    """Match haiku.prelude, including checkouts predating that module."""
    inspiration = description.strip() if found else ""
    if not inspiration:
        return "なんか浮かんできたわ。"
    if any(marker in inspiration for marker in ("浮か", "おもいつ", "思いつ")):
        return (
            inspiration
            if inspiration.endswith(("。", "！", "？", "!", "?"))
            else f"{inspiration}。"
        )
    return f"{inspiration.rstrip('。！？!?')}、なんか浮かんできたわ。"


class _ScenePayload:
    """A single canned reply for the existing scene-domain parser, not an LLM."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.consumed = False

    def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        if self.consumed or request.kind != "haiku_scene":
            raise AssertionError("preparation permits only one canned scene reply")
        self.consumed = True
        return deepcopy(self.payload)


class HaikuPreparation:
    """Strict context -> inspiration -> materials -> emission protocol.

    Disabled LLM jobs go directly from context to emission, and only their
    returned fixed_text may be projected. A completed instance cannot be reused.
    Returned dictionaries are detached copies; callers cannot mutate the job.
    """

    def __init__(self) -> None:
        self._stage = "new"
        self._machine: DogidoStateMachine | None = None
        self._event: GameEvent | None = None
        self._fixed_text: str | None = None

    def handle(self, frame: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(frame, dict):
            raise ValueError("haiku preparation frame must be an object")
        op = frame.get("op")
        if op == "haiku_context":
            output = self._context(frame)
        elif op == "haiku_inspiration":
            output = self._inspiration(frame)
        elif op == "haiku_materials":
            output = self._materials(frame)
        elif op == "haiku_emission":
            output = self._emission(frame)
        else:
            raise ValueError("unsupported haiku preparation operation")
        return deepcopy(output)

    def _require(self, *stages: str) -> tuple[DogidoStateMachine, GameEvent]:
        if self._stage not in stages or self._machine is None or self._event is None:
            raise ValueError(f"invalid haiku preparation stage: {self._stage}")
        return self._machine, self._event

    @staticmethod
    def _request(kind: str, details: dict[str, Any], settings: Settings) -> dict[str, Any]:
        return asdict(StructuredGenerationRequest(
            kind=kind,
            details=details,
            route="chat",
            temperature=0.15 if kind == "haiku_irony" else 0.2,
            max_tokens=settings.haiku_structured_max_tokens,
            fallback_value={"found": False},
        ))

    def _context(self, frame: dict[str, Any]) -> dict[str, Any]:
        if self._stage != "new":
            raise ValueError("haiku context is already captured")
        raw_settings = frame.get("settings", {})
        if not isinstance(raw_settings, dict):
            raise ValueError("settings must be an object")
        values = deepcopy(raw_settings)
        values.setdefault("llm_enabled", True)
        # These are pure projection calls, even if parent runtime enables audio
        # or storage. No backend, worker, client, or memory store is constructed.
        values.update(audio_enabled=False, memory_enabled=False, decision_policy="legacy")
        settings = _PreparationSettings(_env_file=None, **values)
        event = GameEvent.model_validate(deepcopy(frame["event"]))
        machine = DogidoStateMachine(settings, llm=None)
        lessons = deepcopy(frame.get("lessons") or [])
        if not isinstance(lessons, list):
            raise ValueError("lessons must be a list")
        machine.haiku_lessons_provider = lambda: deepcopy(lessons)
        material = self._dialogue_material(frame, event)
        if not isinstance(material, dict):
            raise ValueError("dialogue_material must be an object")
        machine._pending_conversation_haiku_material = material or None
        # This is the only context construction, including weighted item choice.
        context = machine._haiku_context(event)
        machine._pending_haiku_context = context
        machine._pending_haiku_origin_event = event.model_copy(deep=True)
        self._machine, self._event = machine, event
        self._stage = "context"
        if not settings.llm_enabled:
            self._fixed_text = machine._fallback_haiku_line(event)
            machine._pending_haiku_fixed_line = self._fixed_text
            self._seed(IronyContext(), spoken_text=None)
            self._stage = "fixed"
        return {
            "request": self._request("haiku_irony", context.irony_details(), settings)
                if settings.llm_enabled else None,
            "fallback_text": machine._llm_failed_haiku_line(),
            "fixed_text": self._fixed_text,
        }

    @staticmethod
    def _dialogue_material(frame: dict[str, Any], event: GameEvent) -> dict[str, Any]:
        if "dialogue_material" in frame:
            return deepcopy(frame["dialogue_material"] or {})
        turns = frame.get("completed_turns") or []
        if not isinstance(turns, list):
            raise ValueError("completed_turns must be a list")
        foreground = ForegroundDialogue()
        for turn in turns:
            if not isinstance(turn, dict):
                raise ValueError("completed_turns entries must be objects")
            # Presence in this explicit list is the parent's playback receipt;
            # input, tentative replies, and queued speech must not be supplied.
            fields = [turn.get(key, "") for key in ("turn_id", "player_text", "dogido_text")]
            if not all(isinstance(value, str) for value in fields):
                raise ValueError("completed_turn fields must be strings")
            foreground.note_completed_turn(*fields, route="casual", at=event.observed_at)
        foreground.activate("casual", now=event.observed_at)
        return foreground.casual_haiku_material()

    def _seed(self, irony: IronyContext, *, spoken_text: str | None) -> None:
        machine, event = self._require("context")
        context = machine._pending_haiku_context
        assert context is not None
        machine._pending_haiku_irony = irony
        machine._pending_haiku_interpretation = (
            irony.description.strip() if irony.found and irony.description.strip() else None
        )
        atoms = merge_source_atoms(
            context.source_atoms,
            machine._conversation_haiku_source_atoms(
                machine._pending_conversation_haiku_material or {},
            ),
        )
        machine._pending_haiku_source_atoms = atoms
        machine._stash_haiku_materials_seed(
            event, context, irony, SceneContext(),
            source_atoms=atoms, preface_spoken=spoken_text,
        )
        machine._attach_pending_conversation_haiku_materials()
        constraints = machine._haiku_constraint_details(event, SceneContext())
        if constraints and machine._pending_haiku_materials is not None:
            machine._pending_haiku_materials["haiku_constraints"] = constraints

    def _inspiration(self, frame: dict[str, Any]) -> dict[str, Any]:
        machine, _ = self._require("context")
        payload = frame.get("payload")
        try:
            irony = IronyContext.from_mapping(payload)
        except (TypeError, ValueError, OverflowError):
            # Malformed model fields must take the same no-inspiration branch,
            # not terminate a job or replace it with a catalog poem.
            irony = IronyContext()
        spoken = compose_inspiration_speech(found=irony.found, description=irony.description)
        self._seed(irony, spoken_text=spoken)
        context = machine._pending_haiku_context
        assert context is not None
        self._stage = "inspiration"
        return {
            "text": irony.description.strip() if irony.found else "",
            "spoken_text": spoken,
            "request": self._request("haiku_scene", context.scene_details(irony), machine.settings),
        }

    def _materials(self, frame: dict[str, Any]) -> dict[str, Any]:
        machine, event = self._require("inspiration")
        payload = frame.get("payload")
        payload = deepcopy(payload) if isinstance(payload, dict) else {"found": False}
        context = machine._pending_haiku_context
        assert context is not None
        try:
            # Catch malformed container fields before entering the shared parser.
            SceneContext.from_mapping(payload, source_atoms=context.source_atoms)
        except (TypeError, ValueError, OverflowError):
            payload = {"found": False, "__dogido_status": "invalid_payload"}
        canned = _ScenePayload(payload)
        machine.llm = canned
        try:
            machine._prepare_pending_haiku_generation(event)
        finally:
            machine.llm = None
        if not canned.consumed or machine._pending_haiku_prompt_details is None:
            raise AssertionError("haiku material preparation did not produce native input")
        settings = machine.settings
        materials = machine._pending_haiku_materials or {}
        self._stage = "materials"
        return {
            "input": {
                "details": machine._pending_haiku_prompt_details,
                "source_atoms": [atom.to_prompt_dict() for atom in machine._pending_haiku_source_atoms],
                "fallback_text": machine._llm_failed_haiku_line(),
                "max_tokens": settings.haiku_structured_max_tokens,
                "grounding_max_tokens": settings.haiku_grounding_max_tokens,
                "generation_strategy": settings.haiku_generation_strategy,
                "max_regeneration_rounds": settings.haiku_max_regeneration_rounds,
                "llm_enabled": settings.llm_enabled,
            },
            "materials": materials,
            "interpretation": machine._pending_haiku_interpretation,
            "interpretation_origin": materials.get("interpretation_origin"),
        }

    def _emission(self, frame: dict[str, Any]) -> dict[str, Any]:
        machine, event = self._require("materials", "fixed")
        result = frame.get("result")
        if not isinstance(result, dict) or result.get("accepted") is not True:
            raise ValueError("only an accepted native result may become an emission")
        text = result.get("text")
        if not isinstance(text, str) or not text.strip() or machine._is_llm_failed_haiku_text(text):
            raise ValueError("accepted emission requires poem text")
        if self._stage == "fixed":
            if text != self._fixed_text:
                raise ValueError("disabled LLM emission must use the prepared fixed text")
        else:
            line_sources = result.get("line_sources")
            if not isinstance(line_sources, list):
                raise ValueError("native result requires line_sources")
            assert machine._pending_haiku_materials is not None
            machine._pending_haiku_materials.update({
                "line_sources": deepcopy(line_sources),
                "generation_strategy": result["generation_strategy"],
                "regeneration_rounds": result["regeneration_rounds"],
                "prompt_variant": result["prompt_variant"],
            })
        # This method only constructs an in-memory HaikuEmission and links its
        # existing materials. Its timestamp is discarded: Rust owns completion.
        assert machine._pending_haiku_materials
        machine._remember_haiku_emission(event, event.observed_at, text, route="haiku")
        emission = machine.emitted_haiku
        if emission is None:
            raise AssertionError("haiku emission projection was empty")
        self._stage = "emitted"
        return {
            "text": emission.text,
            "surface_text": emission.surface_text,
            "reading_text": emission.reading_text,
            "lines": [line.to_dict() for line in emission.lines],
            "materials": emission.materials,
            "interpretation": emission.interpretation,
            "preface": emission.preface,
            "biome": emission.biome,
            "structure": emission.structure,
            "time_phase": emission.time_phase,
            "dimension": emission.dimension,
            "event_sequence": emission.event_sequence,
            "route": emission.route,
        }
