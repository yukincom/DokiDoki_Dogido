from __future__ import annotations

from pathlib import Path

DEFAULT_CUE_FILES = {
    "spot_hostile_gasp": "freesound_community-male-gasp-1-7183.mp3",
    "panic_scream_start": "universfield-man-scream-08-352438.mp3",
    "suppressed_gasp": "universfield-funny-dramatic-gasp-320975.mp3",
    "suppressed_breath": "freesound_community-heavy-breath-male-63980.mp3",
    "aftermath_relief": "aftermath.wav",
    "panic_multi": "universfield-man-scream-08-352438.mp3",
    "panic_generic": "universfield-man-scream-08-352438.mp3",
    "panic_creeper": "universfield-man-scream-08-352438.mp3",
    "panic_zombie": "universfield-man-scream-08-352438.mp3",
    "panic_skeleton": "universfield-man-scream-08-352438.mp3",
    "panic_spider": "universfield-man-scream-08-352438.mp3",
    "panic_witch": "universfield-man-scream-08-352438.mp3",
    "panic_enderman": "universfield-man-scream-08-352438.mp3",
}


def resolve_cue_path(cue_dir: Path | None, cue_id: str) -> Path | None:
    if cue_dir is None:
        return None

    mapped = DEFAULT_CUE_FILES.get(cue_id)
    if mapped:
        candidate = cue_dir / mapped
        if candidate.exists():
            return candidate

    for suffix in (".wav", ".mp3", ".m4a"):
        candidate = cue_dir / f"{cue_id}{suffix}"
        if candidate.exists():
            return candidate

    return None
