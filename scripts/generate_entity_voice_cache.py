"""コールアウト用の断片音声を VOICEVOX で生成する。

既定: 敵対・中立モブ名 + 体数 + 定型句のみ（友好 pure passive は出さない）。

方針:
  - 戦況コールアウトは全文 TTS より、名称・体数・句のパズル連結を本線にする
  - 生成した mp3 のうち「使うセット」は cue_voice/ に置きコミット可
  - 友好モブ名はコールアウトに不要なので生成しない

使い方:
  python scripts/generate_entity_voice_cache.py
  python scripts/generate_entity_voice_cache.py --overwrite
  python scripts/generate_entity_voice_cache.py --only creeper --only zombie
"""
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
    CALLOUT_MOB_VOICE_LABELS,
    COUNT_FRAGMENT_TEXTS,
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
        "scope": "callout_threat_only",  # hostile + neutral, no pure passive
        "mob": {},
        "common": {
            "counts": {},
            "phrases": {},
        },
    }

    with httpx.Client(timeout=20.0) as client:
        mob_manifest: dict[str, object] = {}
        for entity_id, label in sorted(CALLOUT_MOB_VOICE_LABELS.items()):
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
    print(f"generated callout voice fragments under {root} (threat/neutral only)")
    print(f"manifest: {manifest_path}")
    print(f"mob clips in manifest: {len(manifest.get('mob') or {})}")


if __name__ == "__main__":
    main()
