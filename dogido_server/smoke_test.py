from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from dogido_server.app import create_app
from dogido_server.config import Settings, get_settings
from dogido_server.cues import DEFAULT_CUE_FILES, resolve_cue_path
from dogido_server.models import GameEvent
from dogido_server.service import DogidoService
from dogido_server.state_machine import AudioAction


def main() -> None:
    parser = argparse.ArgumentParser(description="Dogido first-run smoke test")
    parser.add_argument(
        "--mode",
        choices=("diagnose", "preview", "replay", "http", "all"),
        default="all",
        help="Which smoke test mode to run",
    )
    parser.add_argument(
        "--audio",
        action="store_true",
        help="Actually play cue/TTS audio during preview and replay",
    )
    parser.add_argument(
        "--fixture-dir",
        default="fixtures",
        help="Directory containing event fixture JSON files",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.mode in {"diagnose", "all"}:
        diagnose(settings)
    if args.mode in {"preview", "all"}:
        preview(settings, with_audio=args.audio)
    if args.mode in {"replay", "all"}:
        replay(settings, Path(args.fixture_dir), with_audio=args.audio)
    if args.mode in {"http", "all"}:
        http_smoke(settings)


def diagnose(settings: Settings) -> None:
    print("[diagnose] settings")
    print(f"  - bind: {settings.bind_host}:{settings.bind_port}")
    print(f"  - decision_policy: {settings.decision_policy}")
    print(f"  - llm_enabled: {settings.llm_enabled}")
    print(f"  - llm_backend: {settings.llm_backend}")
    print(f"  - llm_provider: {settings.llm_provider}")
    print(f"  - mlx_model_id: {settings.mlx_model_id}")
    print(f"  - llm_base_url: {settings.llm_base_url}")
    print(f"  - llm_resolved_base_url: {settings.llm_resolved_base_url}")
    print(f"  - llm_model: {settings.llm_model}")
    chat_settings = settings.llm_route_settings("chat")
    haiku_settings = settings.llm_route_settings("haiku")
    print(
        "  - llm_chat_route: "
        f"backend={chat_settings.llm_effective_backend} provider={chat_settings.llm_provider} "
        f"model={chat_settings.llm_model or chat_settings.mlx_model_id or 'unset'}"
    )
    print(
        "  - llm_haiku_route: "
        f"backend={haiku_settings.llm_effective_backend} provider={haiku_settings.llm_provider} "
        f"model={haiku_settings.llm_model or haiku_settings.mlx_model_id or 'unset'}"
    )
    print(f"  - tts_backend: {settings.tts_backend}")
    print(f"  - cue_backend: {settings.cue_backend}")
    print(f"  - cue_audio_dir: {settings.cue_audio_dir}")
    print(f"  - voicevox_speaker: {settings.voicevox_speaker}")

    print("[diagnose] binaries")
    for binary in ("say", "afplay"):
        location = shutil.which(binary)
        print(f"  - {binary}: {'ok ' + location if location else 'missing'}")

    print("[diagnose] cue files")
    for cue_id, filename in DEFAULT_CUE_FILES.items():
        resolved = resolve_cue_path(settings.cue_audio_dir, cue_id)
        status = "ok" if resolved else f"missing ({filename})"
        print(f"  - {cue_id}: {status}")

    print("[diagnose] voicevox")
    if settings.tts_backend != "voicevox":
        print("  - skipped (tts_backend is not voicevox)")
        return
    try:
        with httpx.Client(timeout=3.0) as client:
            response = client.get(f"{settings.voicevox_url}/version")
            response.raise_for_status()
        print(f"  - ok ({response.text.strip()})")
    except Exception as exc:
        print(f"  - unavailable ({exc})")


def preview(settings: Settings, with_audio: bool) -> None:
    print("[preview] cue and speech")
    service = DogidoService(settings.model_copy(update={"audio_enabled": with_audio}))
    actions = [
        AudioAction(layer="panic_cue", interrupt=True, cue_id="spot_hostile_gasp", text="ハッ"),
        AudioAction(layer="panic_cue", interrupt=True, cue_id="panic_scream_start", text="うわああ！"),
        AudioAction(layer="panic_cue", interrupt=True, cue_id="suppressed_gasp", text="ヒイッ！"),
        AudioAction(layer="panic_cue", interrupt=False, cue_id="suppressed_breath", text="ハァハァ……"),
        AudioAction(layer="speech", interrupt=False, cue_id="aftermath_relief", text="こわかった……"),
        AudioAction(layer="speech", interrupt=False, text="ドギド起動テストやで。"),
    ]
    for action in actions:
        print(f"  - {action.layer}: cue={action.cue_id} text={action.text}")
    if with_audio:
        service.dispatch_actions(actions)
        print("  - audio played")
    else:
        print("  - audio skipped (--audio で再生)")


def replay(settings: Settings, fixture_dir: Path, with_audio: bool) -> None:
    print("[replay] fixtures")
    service = DogidoService(settings.model_copy(update={"audio_enabled": with_audio}))
    if not fixture_dir.exists():
        print(f"  - missing fixture dir: {fixture_dir}")
        return

    for path in sorted(fixture_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        event = GameEvent.model_validate(payload)
        result = service.process_event(event, session_id="smoke_replay")
        mode = result.response.state.mode if result.response.state else "n/a"
        print(
            f"  - {path.name}: mode={mode} "
            f"panic={bool(result.response.outputs and result.response.outputs.panic_cue_enqueued)} "
            f"callout={bool(result.response.outputs and result.response.outputs.callout_enqueued)} "
            f"speech={bool(result.response.outputs and result.response.outputs.speech_enqueued)}"
        )
        for action in result.actions:
            print(f"      * {action.layer}: cue={action.cue_id} text={action.text}")
        if with_audio and result.actions:
            service.dispatch_actions(result.actions)


def http_smoke(settings: Settings) -> None:
    print("[http] in-process api")
    app = create_app(settings.model_copy(update={"audio_enabled": False}))
    client = TestClient(app)

    healthz = client.get("/healthz")
    print(f"  - healthz: {healthz.status_code} {healthz.json()}")

    session = client.post(
        "/api/v1/adapter-sessions",
        json={
            "adapter_name": "dogido-fabric-client",
            "adapter_version": "0.1.0",
            "game": "minecraft-java",
            "schema_version": settings.accepted_schema_version,
            "player_name": "main_player",
            "profile_name": "default",
            "capabilities": ["visual_threats", "auditory_threats", "inventory"],
        },
    )
    body = session.json()
    session_id = body["session_id"]
    print(f"  - create session: {session.status_code} {session_id}")

    threat_fixture = Path("fixtures/creeper_behind_close.json")
    payload = json.loads(threat_fixture.read_text(encoding="utf-8"))
    event_response = client.post(
        "/api/v1/game-events",
        headers={"X-Dogido-Session-Id": session_id},
        json=payload,
    )
    event_body = event_response.json()
    print(
        "  - post event: "
        f"{event_response.status_code} mode={event_body['state']['mode']} "
        f"panic={event_body['outputs']['panic_cue_enqueued']} "
        f"callout={event_body['outputs']['callout_enqueued']}"
    )


if __name__ == "__main__":
    main()
