from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dogido_server.config import Settings
from dogido_server.entity_voice_catalog import (
    COUNT_FRAGMENT_TEXTS,
    MOB_VOICE_LABELS,
    PHRASE_FRAGMENT_TEXTS,
)

FFMPEG_BIN = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def ensure_voice_file(
    client: httpx.Client,
    settings: Settings,
    text: str,
    output_path: Path,
    overwrite: bool = False,
) -> None:
    if output_path.exists() and not overwrite:
        return

    query = client.post(
        f"{settings.voicevox_url}/audio_query",
        params={"speaker": settings.voicevox_speaker, "text": text},
    )
    query.raise_for_status()
    payload = query.json()
    payload["speedScale"] = settings.voicevox_speed_scale
    payload["pitchScale"] = settings.voicevox_pitch_scale
    payload["volumeScale"] = settings.voicevox_volume_scale
    if settings.voicevox_output_sampling_rate is not None:
        payload["outputSamplingRate"] = settings.voicevox_output_sampling_rate

    synth = client.post(
        f"{settings.voicevox_url}/synthesis",
        params={"speaker": settings.voicevox_speaker},
        json=payload,
    )
    synth.raise_for_status()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_audio_content(synth.content, output_path)


def _write_audio_content(content: bytes, output_path: Path) -> None:
    if output_path.suffix.lower() != ".mp3":
        output_path.write_bytes(content)
        return
    subprocess.run(
        [
            FFMPEG_BIN,
            "-y",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(output_path),
        ],
        input=content,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def generate_catalog(
    root: Path,
    settings: Settings,
    *,
    overwrite: bool = False,
    only_ids: set[str] | None = None,
) -> dict[str, object]:
    manifest: dict[str, object] = {
        "speaker": settings.voicevox_speaker,
        "voicevox_url": settings.voicevox_url,
        "generated_root": str(root),
        "mob": {},
        "common": {
            "counts": {},
            "phrases": {},
        },
    }

    with httpx.Client(timeout=20.0) as client:
        mob_manifest: dict[str, object] = {}
        for entity_id, label in MOB_VOICE_LABELS.items():
            relative = Path("mob") / f"{entity_id}.mp3"
            mob_manifest[entity_id] = {
                "label": label,
                "path": str(relative),
            }
            if only_ids is not None and entity_id not in only_ids:
                continue
            ensure_voice_file(client, settings, label, root / relative, overwrite=overwrite)
        manifest["mob"] = mob_manifest

        counts_manifest: dict[str, object] = {}
        for key, text in COUNT_FRAGMENT_TEXTS.items():
            relative = Path("common") / "counts" / f"{key}.mp3"
            counts_manifest[key] = {"text": text, "path": str(relative)}
            if only_ids is not None and key not in only_ids:
                continue
            ensure_voice_file(client, settings, text, root / relative, overwrite=overwrite)
        manifest["common"]["counts"] = counts_manifest  # type: ignore[index]

        phrases_manifest: dict[str, object] = {}
        for key, text in PHRASE_FRAGMENT_TEXTS.items():
            relative = Path("common") / "phrases" / f"{key}.mp3"
            phrases_manifest[key] = {"text": text, "path": str(relative)}
            if only_ids is not None and key not in only_ids:
                continue
            ensure_voice_file(client, settings, text, root / relative, overwrite=overwrite)
        manifest["common"]["phrases"] = phrases_manifest  # type: ignore[index]

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()

    settings = Settings()
    root = Path("cue_voice")
    only_ids = set(args.only) if args.only else None
    manifest = generate_catalog(root, settings, overwrite=args.overwrite, only_ids=only_ids)
    manifest_path = root / "entity_cache_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"generated entity voice cache under {root}")
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
