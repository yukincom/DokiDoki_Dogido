from __future__ import annotations

from pathlib import Path

DEFAULT_CUE_FILES = {
    "spot_hostile_gasp": "panic/freesound_community-male-gasp-1-7183.mp3",
    "panic_scream_start": "panic/universfield-man-scream-08-352438.mp3",
    "front_spawn_scream": "panic/universfield-man-scream-08-352438.mp3",
    "ushiro_scream": "panic/universfield-man-scream-08-352438.mp3",
    "suppressed_gasp": "panic/universfield-funny-dramatic-gasp-320975.mp3",
    "suppressed_breath": "panic/freesound_community-heavy-breath-male-63980.mp3",
    "aftermath_relief": "aftermath.mp3",
    "panic_multi": "panic/universfield-man-scream-08-352438.mp3",
    "panic_generic": "panic/universfield-man-scream-08-352438.mp3",
    "panic_creeper": "panic/universfield-man-scream-08-352438.mp3",
    "panic_zombie": "panic/universfield-man-scream-08-352438.mp3",
    "panic_skeleton": "panic/universfield-man-scream-08-352438.mp3",
    "panic_spider": "panic/universfield-man-scream-08-352438.mp3",
    "panic_witch": "panic/universfield-man-scream-08-352438.mp3",
    "panic_enderman": "panic/universfield-man-scream-08-352438.mp3",
}


def resolve_cue_path(cue_dir: Path | None, cue_id: str) -> Path | None:
    if cue_dir is None:
        return None

    mapped = DEFAULT_CUE_FILES.get(cue_id)
    if mapped:
        candidate = cue_dir / mapped
        if candidate.exists():
            return candidate

    for suffix in (".mp3", ".wav", ".m4a"):
        candidate = cue_dir / f"{cue_id}{suffix}"
        if candidate.exists():
            return candidate

    return None
