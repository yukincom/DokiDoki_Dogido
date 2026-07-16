from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dogido_server.minecraft_ids import normalize_minecraft_id
from dogido_server.memory_types import HaikuEmission
from dogido_server.models import GameEvent

if TYPE_CHECKING:
    from dogido_server.state_machine.types import AudioAction

LOGGER = logging.getLogger("uvicorn.error")

PROGRESS_ITEMS: dict[str, str] = {
    "story/mine_diamond": "ダイヤモンド！",
    "story/enter_the_end": "おしまい？",
    "nether/root": "ネザー",
    "end/elytra": "空はどこまでも高く",
}


def datetime_json(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def normalize_advancement_id(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.startswith("minecraft:"):
        return normalized.split(":", 1)[1]
    return normalized


class MemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.short_term_dir = root / "short_term"
        self.long_term_dir = root / "long_term"
        self.short_term_path = self.short_term_dir / "current_session.jsonl"
        self.rolling_summary_path = self.short_term_dir / "rolling_summary.json"
        self.haiku_entries_path = self.long_term_dir / "haiku_entries.jsonl"
        self.haiku_revisions_path = self.long_term_dir / "haiku_revisions.jsonl"
        self.catalog_corrections_path = self.long_term_dir / "catalog_corrections.jsonl"
        self.player_profile_path = self.long_term_dir / "player_profile.json"

    def append_short_term_event(self, payload: dict[str, Any]) -> None:
        self._append_jsonl(self.short_term_path, payload)

    def append_player_input(self, event: GameEvent, session_id: str, text: str) -> None:
        self.append_short_term_event(
            {
                "type": "player_input",
                "time": datetime_json(event.observed_at),
                "session_id": session_id,
                "sequence": event.sequence,
                "text": text,
                "biome": normalize_minecraft_id(event.world.biome),
                "structure": normalize_minecraft_id(event.world.structure),
            }
        )

    def append_speech_action(self, event: GameEvent, session_id: str, action: AudioAction) -> None:
        self.append_short_term_event(
            {
                "type": "dogido_speech",
                "time": datetime_json(event.observed_at),
                "session_id": session_id,
                "sequence": event.sequence,
                "layer": action.layer,
                "cue_id": action.cue_id,
                "text": action.text,
                "biome": normalize_minecraft_id(event.world.biome),
                "structure": normalize_minecraft_id(event.world.structure),
            }
        )

    def append_haiku_emission(self, session_id: str, emission: HaikuEmission) -> None:
        self.append_short_term_event(
            {
                "type": "haiku_emitted",
                "time": datetime_json(emission.created_at),
                "session_id": session_id,
                "sequence": emission.event_sequence,
                "text": emission.text,
                "interpretation": emission.interpretation,
                "biome": emission.biome,
                "structure": emission.structure,
            }
        )

    def save_agent_haiku(self, emission: HaikuEmission) -> tuple[dict[str, Any], bool]:
        entry = self._haiku_entry_from_emission(emission)
        return self._append_unique_haiku_entry(entry)

    def save_player_haiku(self, event: GameEvent, text: str) -> tuple[dict[str, Any], bool]:
        created_at = event.observed_at
        entry = {
            "id": self._haiku_id(created_at, event.sequence, suffix="player"),
            "created_at": datetime_json(created_at),
            "author": "player",
            "kind": "player_haiku",
            "text": text.strip(),
            "preface": None,
            "interpretation": None,
            "world": self._world_payload(event),
            "trigger": {
                "event_sequence": event.sequence,
                "route": None,
            },
        }
        return self._append_unique_haiku_entry(entry)

    def list_haiku_entries(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.haiku_entries_path)

    def list_haiku_revisions(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.haiku_revisions_path)

    def save_haiku_feedback(
        self,
        emission: HaikuEmission,
        *,
        revised_text: str | None = None,
        comment: str | None = None,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        """元句を長期に残し、直し句があれば revision にペア保存する。

        プロンプト常駐用ではない。明示検索・保存用の長期アーカイブ。
        """
        entry, _ = self.save_agent_haiku(emission)
        created_at = observed_at or emission.created_at
        revision = {
            "id": self._revision_id(created_at, emission.event_sequence),
            "created_at": datetime_json(created_at),
            "haiku_id": entry.get("id"),
            "source": "player_feedback",
            "comment": (comment or "").strip() or None,
            "original_text": emission.text.strip(),
            "revised_text": (revised_text or "").strip() or None,
            "world": {
                "biome": emission.biome,
                "structure": emission.structure,
                "time_phase": emission.time_phase,
                "dimension": emission.dimension,
            },
        }
        self._append_jsonl(self.haiku_revisions_path, revision)
        return revision

    def save_reading_correction(
        self,
        *,
        surface: str,
        reading: str,
        wrong_reading: str | None = None,
        source: str | None = None,
        observed_at: datetime | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """読みの訂正をオーバーレイ JSONL に追記し、ランタイム辞書を更新する。"""
        from dogido_server.catalog_readings import apply_overlay_correction, configure_corrections_path

        created_at = observed_at or datetime.now().astimezone()
        row = {
            "id": f"corr_{created_at.strftime('%Y%m%d_%H%M%S')}_{surface}",
            "created_at": datetime_json(created_at),
            "surface": surface.strip(),
            "reading": reading.strip(),
            "wrong_reading": (wrong_reading or "").strip() or None,
            "forbidden_readings": [wrong_reading.strip()] if wrong_reading and wrong_reading.strip() else [],
            "source": source,
            "session_id": session_id,
        }
        self._append_jsonl(self.catalog_corrections_path, row)
        configure_corrections_path(self.catalog_corrections_path)
        apply_overlay_correction(
            surface=surface,
            reading=reading,
            wrong_reading=wrong_reading,
            source=source,
        )
        return row

    def search_haiku_memory(
        self,
        *,
        biome: str | None = None,
        biome_ids: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None = None,
        query_text: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """明示的な「あの句」想起用。場所・壁時計の期間で長期句を探す。

        ゲーム内時刻は見ない。`created_at`（保存時の実時刻）だけを使う。
        biome_ids はカタロググループ展開後の集合（寒いところ＝cold+snowy など）。
        """
        biome_key = normalize_minecraft_id(biome) if biome else None
        allowed_biomes: set[str] | None = None
        if biome_ids:
            allowed_biomes = {
                normalized
                for raw in biome_ids
                if (normalized := normalize_minecraft_id(str(raw) if raw is not None else None))
            }
            if not allowed_biomes:
                allowed_biomes = None
        if biome_key:
            allowed_biomes = {biome_key} if allowed_biomes is None else (allowed_biomes | {biome_key})
        query = (query_text or "").strip()
        hits: list[dict[str, Any]] = []
        seen_texts: set[str] = set()

        def _parse_created(value: object) -> datetime | None:
            if value is None:
                return None
            text = str(value).strip()
            if not text:
                return None
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed

        def _in_time_range(created: datetime | None) -> bool:
            if since is None and until is None:
                return True
            if created is None:
                return False
            # 比較用に aware へ
            created_cmp = created if created.tzinfo is not None else created.replace(tzinfo=timezone.utc)
            if since is not None:
                since_cmp = since if since.tzinfo is not None else since.replace(tzinfo=timezone.utc)
                if created_cmp < since_cmp:
                    return False
            if until is not None:
                until_cmp = until if until.tzinfo is not None else until.replace(tzinfo=timezone.utc)
                if created_cmp >= until_cmp:
                    return False
            return True

        def _match(world: dict[str, Any], created_at: object, *texts: object) -> bool:
            if not _in_time_range(_parse_created(created_at)):
                return False
            row_biome = normalize_minecraft_id(str(world.get("biome") or "") or None)
            if allowed_biomes is not None:
                if not row_biome or row_biome not in allowed_biomes:
                    return False
            if not query:
                return True
            blob = " ".join(str(part) for part in texts if part)
            return query in blob

        def _add(hit: dict[str, Any]) -> bool:
            key = str(hit.get("original_text") or "").strip()
            if key and key in seen_texts:
                return False
            if key:
                seen_texts.add(key)
            hits.append(hit)
            return len(hits) >= limit

        # 新しい順: created_at でソート（revision と entry を混ぜる）
        candidates: list[tuple[datetime, dict[str, Any]]] = []

        for rev in self.list_haiku_revisions():
            world = rev.get("world") if isinstance(rev.get("world"), dict) else {}
            if not _match(world, rev.get("created_at"), rev.get("original_text"), rev.get("revised_text"), rev.get("comment")):
                continue
            created = _parse_created(rev.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)
            candidates.append(
                (
                    created,
                    {
                        "kind": "revision",
                        "id": rev.get("id"),
                        "created_at": rev.get("created_at"),
                        "original_text": rev.get("original_text"),
                        "revised_text": rev.get("revised_text"),
                        "comment": rev.get("comment"),
                        "world": world,
                    },
                )
            )

        for entry in self.list_haiku_entries():
            world = entry.get("world") if isinstance(entry.get("world"), dict) else {}
            text = str(entry.get("text") or "")
            if not _match(world, entry.get("created_at"), text, entry.get("interpretation")):
                continue
            created = _parse_created(entry.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)
            candidates.append(
                (
                    created,
                    {
                        "kind": "entry",
                        "id": entry.get("id"),
                        "created_at": entry.get("created_at"),
                        "original_text": text,
                        "revised_text": None,
                        "comment": entry.get("interpretation"),
                        "world": world,
                    },
                )
            )

        candidates.sort(key=lambda item: item[0], reverse=True)
        for _, hit in candidates:
            if _add(hit):
                break
        return hits

    def load_profile(self, player_name: str | None = None) -> dict[str, Any]:
        if self.player_profile_path.exists():
            try:
                with self.player_profile_path.open("r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                LOGGER.warning("memory_profile_load_failed path=%s detail=%s", self.player_profile_path, exc)
                loaded = {}
        else:
            loaded = {}
        profile = self._default_profile(player_name or str(loaded.get("player_name") or "main_player"))
        profile.update({key: value for key, value in loaded.items() if key != "progress"})
        progress = profile["progress"]
        for key, value in (loaded.get("progress") or {}).items():
            if key in progress and isinstance(value, dict):
                progress[key].update(value)
        return profile

    def record_progress(self, player_name: str | None, advancement_ids: list[str], observed_at: datetime) -> bool:
        matched = [
            normalized
            for normalized in (normalize_advancement_id(advancement_id) for advancement_id in advancement_ids)
            if normalized in PROGRESS_ITEMS
        ]
        if not matched:
            return False
        profile = self.load_profile(player_name)
        changed = False
        for advancement_id in matched:
            progress = profile["progress"][advancement_id]
            if progress.get("unlocked"):
                continue
            progress["unlocked"] = True
            progress["first_unlocked_at"] = datetime_json(observed_at)
            changed = True
        if changed:
            self._write_json(self.player_profile_path, profile)
        return changed

    def load_rolling_summary(self) -> dict[str, Any]:
        if not self.rolling_summary_path.exists():
            return {
                "updated_at": None,
                "startup_summary": "",
                "open_topics": [],
                "recent_tone": "",
            }
        try:
            with self.rolling_summary_path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("memory_summary_load_failed path=%s detail=%s", self.rolling_summary_path, exc)
            return {
                "updated_at": None,
                "startup_summary": "",
                "open_topics": [],
                "recent_tone": "",
            }
        return loaded if isinstance(loaded, dict) else {}

    def _haiku_entry_from_emission(self, emission: HaikuEmission) -> dict[str, Any]:
        return {
            "id": self._haiku_id(emission.created_at, emission.event_sequence),
            "created_at": datetime_json(emission.created_at),
            "author": "dogido",
            "kind": "agent_haiku",
            "text": emission.text.strip(),
            "preface": emission.preface,
            "interpretation": emission.interpretation,
            "world": {
                "biome": emission.biome,
                "structure": emission.structure,
                "time_phase": emission.time_phase,
                "dimension": emission.dimension,
            },
            "trigger": {
                "event_sequence": emission.event_sequence,
                "route": emission.route,
            },
        }

    def _world_payload(self, event: GameEvent) -> dict[str, str | None]:
        time_phase = getattr(event.world.time_phase, "value", event.world.time_phase)
        return {
            "biome": normalize_minecraft_id(event.world.biome),
            "structure": normalize_minecraft_id(event.world.structure),
            "time_phase": str(time_phase) if time_phase else None,
            "dimension": event.player.dimension,
        }

    def _append_unique_haiku_entry(self, entry: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        entry_id = str(entry.get("id") or "")
        if entry_id and self._jsonl_contains_id(self.haiku_entries_path, entry_id):
            return entry, False
        self._append_jsonl(self.haiku_entries_path, entry)
        return entry, True

    def _haiku_id(self, created_at: datetime, sequence: int | None, suffix: str | None = None) -> str:
        timestamp = created_at.strftime("%Y%m%d_%H%M%S")
        sequence_part = str(sequence) if sequence is not None else created_at.strftime("%f")
        parts = ["hk", timestamp, sequence_part]
        if suffix:
            parts.append(suffix)
        return "_".join(parts)

    def _revision_id(self, created_at: datetime, sequence: int | None) -> str:
        timestamp = created_at.strftime("%Y%m%d_%H%M%S")
        sequence_part = str(sequence) if sequence is not None else created_at.strftime("%f")
        return f"rev_{timestamp}_{sequence_part}"

    def _default_profile(self, player_name: str) -> dict[str, Any]:
        return {
            "player_name": player_name,
            "progress": {
                advancement_id: {
                    "label": label,
                    "unlocked": False,
                    "first_unlocked_at": None,
                }
                for advancement_id, label in PROGRESS_ITEMS.items()
            },
        }

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(self._json_ready(payload), ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(self._json_ready(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    LOGGER.warning("memory_jsonl_skip_invalid path=%s line=%s", path, stripped[:120])
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
        return rows

    def _jsonl_contains_id(self, path: Path, entry_id: str) -> bool:
        return any(str(row.get("id") or "") == entry_id for row in self._read_jsonl(path))

    def _json_ready(self, payload: Any) -> Any:
        if isinstance(payload, datetime):
            return datetime_json(payload)
        if isinstance(payload, dict):
            return {str(key): self._json_ready(value) for key, value in payload.items()}
        if isinstance(payload, list):
            return [self._json_ready(value) for value in payload]
        if hasattr(payload, "__dataclass_fields__"):
            return self._json_ready(asdict(payload))
        return payload
