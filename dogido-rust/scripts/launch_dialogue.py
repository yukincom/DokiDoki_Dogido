#!/usr/bin/env python3
"""Rust本体・音声入力の共通起動。設定を読み、準備済み実行ファイルへ引き渡す。"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import sys
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from runtime_build import check_binary_freshness
from dogido_server.config import get_settings
from dogido_server.runtime_settings import COMBAT_DEFAULTS, SERVER_DEFAULTS


def check_runtime_files(root):
    binary = root / "dogido-rust/target/release/dogido-rust"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError("Rust本体が未準備です。先にreleaseビルドを行ってください。")
    for relative in (
        "dogido-rust/scripts/tts_unidic_adapter.py",
        "dogido-rust/scripts/tts_shared_tokens.py",
        "dogido-rust/scripts/web_adapter.py",
        "dogido_server/runtime_defaults.json",
        "dogido-rust/scripts/haiku_tokens.py",
        "dogido-rust/scripts/workshop_helper.py",
        "dogido-rust/scripts/combat_input_helper.py",
        "dogido_server/language_dialogue/source_cards.json",
        "reference/language_education_and_poetry",
    ):
        if not (root / relative).exists():
            raise RuntimeError(f"起動用の資料が不足しています: {relative}")
    check_binary_freshness(root, binary)
    return binary


def model_enabled(settings):
    routes = [settings.llm_route_settings(route) for route in ("chat", "haiku")]
    active = [route.llm_effective_backend != "noop" for route in routes]
    if settings.llm_enabled and active[0] != active[1]:
        raise ValueError("Rust起動ではchat／haikuの片方だけをnoopにする設定は未対応です。")
    return settings.llm_enabled and all(active)


def voice_settings(settings, folder):
    # 既存AEC環境を再利用。Rust worktreeへ環境を複製・新規導入しない。
    if settings.voice_echo_python is None:
        settings.voice_echo_python = folder / ".dogido_tools/echo-cancel/.venv/bin/python"
    if settings.voice_echo_helper is None:
        settings.voice_echo_helper = folder / ".dogido_tools/echo-cancel/bin/dogido-audio-capture"
    for key in ("voice_echo_python", "voice_echo_helper"):
        path = Path(getattr(settings, key))
        setattr(settings, key, path if path.is_absolute() else folder / path)
    return settings


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--settings-dir", type=Path, required=True)
    p.add_argument("--voice", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--port", type=int, help="この起動だけの待受ポート")
    p.add_argument("--memory-dir", type=Path, help="この起動だけの記憶保存先")
    p.add_argument("--aec", action="store_true", help="既存AECキャプチャを使用")
    p.add_argument("--silence-ms", type=int, help="この音声起動だけの無音区切り")
    args = p.parse_args()
    folder = args.settings_dir.resolve()
    os.chdir(folder)
    settings = get_settings()
    if args.port is not None:
        if not 1 <= args.port <= 65535:
            raise ValueError("ポートは1〜65535で指定してください。")
        settings.bind_port = args.port
    if args.memory_dir is not None:
        settings.memory_dir = args.memory_dir
    if args.aec:
        settings.voice_echo_cancellation = "webrtc"
    if args.silence_ms is not None:
        if args.silence_ms <= 0:
            raise ValueError("無音区切りは正のミリ秒で指定してください。")
        settings.voice_silence_ms = args.silence_ms
    host = "127.0.0.1" if settings.bind_host == "localhost" else settings.bind_host
    if not ipaddress.ip_address(host).is_loopback:
        raise ValueError("ドギドの待受はloopback専用です。DOGIDO_BIND_HOSTに127.0.0.1 または ::1 を指定してください。")
    client_host = host
    url_host = f"[{client_host}]" if ":" in client_host else client_host
    listen_host = f"[{host}]" if ":" in host else host
    base_url = f"http://{url_host}:{settings.bind_port}"
    listen = f"{listen_host}:{settings.bind_port}"

    if args.voice:
        settings = voice_settings(settings, folder)
        from dogido_server.voice_capture import capture_command as resolve_capture_command
        from dogido_server.voice_settings import (
            resolve_whisper_paths, resolve_vad_paths, NORMAL_STT_PROMPT, HAIKU_WORKSHOP_STT_PROMPT,
        )
        cli, model = resolve_whisper_paths(settings)
        capture_command = resolve_capture_command(settings)
        vad = resolve_vad_paths(settings, cli)
        voice_config = {
            "capture_command": capture_command,
            "whisper_cli": str(cli.resolve()), "whisper_model": str(model.resolve()),
            "vad": {"cli": str(vad[0].resolve()), "model": str(vad[1].resolve()),
                    "threshold": settings.voice_vad_threshold} if vad else None,
            "base_url": base_url,
            "rms_threshold": settings.voice_rms_threshold,
            "silence_ms": settings.voice_silence_ms,
            "minimum_ms": settings.voice_min_speech_ms,
            "maximum_ms": int(settings.voice_max_speech_sec * 1000),
            "max_pending": settings.voice_stt_max_pending_segments,
            "max_age_sec": settings.voice_stt_max_segment_age_sec,
            "no_speech_threshold": settings.voice_no_speech_thold,
            "retry_threshold": settings.voice_no_speech_retry_thold,
            "wake_word": settings.voice_wake_word.strip(),
            "normal_prompt": NORMAL_STT_PROMPT, "workshop_prompt": HAIKU_WORKSHOP_STT_PROMPT,
            "use_gpu": True,
        }
        print(f"Rust音声入力: {model.name} / AEC {settings.voice_echo_cancellation} / VAD {'あり' if vad else 'なし'}", flush=True)
        print(f"発話区切り: 無音 {settings.voice_silence_ms}ms", flush=True)
        print(f"配送先: {base_url} / 終了はこのターミナルで Ctrl+C", flush=True)
        if not args.check:
            with urllib.request.urlopen(base_url + "/healthz", timeout=3) as r:
                health = json.load(r)
            if health.get("runtime") != "rust" or health.get("dialogue_ready") is not True:
                raise RuntimeError("先に同じ設定でRust本体を起動してください。")
        binary = check_runtime_files(ROOT)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        if settings.auth_token: env["DOGIDO_AUTH_TOKEN"] = settings.auth_token
        else: env.pop("DOGIDO_AUTH_TOKEN", None)
        command = [str(binary), "voice-input", "--settings", json.dumps(voice_config)]
        if args.check: command.append("--check")
        os.chdir(ROOT)
        # 起動設定だけPythonで解決し、以降の録音／STT／配送／停止はRustに渡す。
        os.execve(binary, command, env)
    base = settings.llm_chat_base_url or settings.llm_base_url or "http://127.0.0.1:8080/v1"
    model = settings.llm_chat_model or settings.llm_model or "default_model"
    use_model = model_enabled(settings)
    haiku_base = settings.llm_haiku_base_url or settings.llm_base_url or base
    haiku_model = settings.llm_haiku_model or settings.llm_model or model
    haiku_settings = {
        "llm_enabled": use_model, "interval_ms": settings.haiku_interval_ms,
        "quiet_time_ms": settings.haiku_quiet_time_ms,
        "structured_max_tokens": settings.haiku_structured_max_tokens,
        "grounding_max_tokens": settings.haiku_grounding_max_tokens,
        "generation_strategy": settings.haiku_generation_strategy,
        "max_regeneration_rounds": settings.haiku_max_regeneration_rounds,
        "base_url": haiku_base, "model": haiku_model,
        "max_tokens": settings.llm_haiku_max_tokens or settings.llm_max_tokens,
        "timeout_ms": int(1000 * (settings.llm_haiku_timeout_sec or settings.llm_timeout_sec)),
        "memory_enabled": settings.memory_enabled,
        "low_threat_resume_delay_ms": settings.workshop_low_threat_resume_delay_ms,
        "platform_ai": {k: getattr(settings, "platform_ai_" + k) for k in (
            "provider", "timeout_sec", "refresh_sec", "failure_cooldown_sec", "foundry_model_alias", "allow_model_download")},
        "memory_dir": str(settings.memory_dir.resolve()),
    }
    for url in (base, haiku_base, settings.voicevox_url):
        if urlsplit(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("この起動ファイルは既存のlocalhostモデル・VOICEVOX専用です。")
    combat_settings = {key: getattr(settings, key) for key in COMBAT_DEFAULTS}
    server_settings = {key: getattr(settings, key) for key in SERVER_DEFAULTS}
    from dogido_server.language_dialogue.main_web import inspect_main_web_availability
    web_availability = inspect_main_web_availability()
    web_settings = {"enabled": settings.main_language_web_enabled and settings.main_language_dialogue_enabled,
                    "available": web_availability.available}
    print(f"同意済みWeb検索: {web_availability.reason if web_settings['enabled'] else 'disabled'}（ブラウザー未起動）", flush=True)
    print(f"Rust本体 / 実行元: {ROOT}", flush=True)
    print(f"設定の読込元: {folder} / モデル: {model}", flush=True)
    print(f"表示: {base_url}/rust-chat", flush=True)
    print("会話・川柳・戦闘・環境反応の判断と音声配送はRustで処理します。", flush=True)
    print(f"自動川柳: {haiku_model} / 保存先: {haiku_settings['memory_dir']}（セッションごと）", flush=True)
    print("情景発話・発句・保存・掛け軸、句の共同編集・採否・読み訂正・保存した句の検索、知識回答・限定国語対話に対応。Web連携は利用前提が揃うときだけ、同意と案内音声の再生完了後に開始します。", flush=True)
    print(f"マイクは同じ設定・ポート {settings.bind_port} の起動指定に --voice を付け、別のターミナルで開始してください。", flush=True)
    print("終了はこのターミナルで Ctrl+C。共有MLXとVOICEVOX本体は停止しません。", flush=True)
    if args.check:
        check_runtime_files(ROOT)
        from tts_shared_tokens import handle as tokens_handle
        from combat_input_helper import Worker as CombatInputWorker
        print("Rust本体・資料・補助のimportを確認しました。辞書・端末AIの初期化はしていません。モデル生成・録音・サーバー起動は行っていません。")
        return
    binary = check_runtime_files(ROOT)
    env = dict(os.environ)
    # 認証値は引数や端末表示へ出さない。
    for name, value in {"DOGIDO_AUTH_TOKEN": settings.auth_token,
                        "DOGIDO_LLM_API_KEY": settings.llm_chat_api_key or settings.llm_api_key,
                        "DOGIDO_LLM_HAIKU_API_KEY": settings.llm_haiku_api_key or settings.llm_api_key}.items():
        if value: env[name] = value
        else: env.pop(name, None)
    command = [str(binary), "serve-dialogue", "--listen", listen, "--python", sys.executable,
        "--helper", str(ROOT / "dogido-rust/scripts/haiku_tokens.py"),
        "--model", model, "--base-url", base, "--voicevox-url", settings.voicevox_url,
        "--speaker", str(settings.voicevox_speaker), "--speed", str(settings.voicevox_speed_scale_peace),
        "--haiku-speed", str(settings.voicevox_speed_scale_haiku),
        "--server-settings", json.dumps(server_settings),
        "--combat-settings", json.dumps(combat_settings),
        "--haiku-settings", json.dumps(haiku_settings),
        "--web-settings", json.dumps(web_settings),
        "--warning-settings", json.dumps({
            **{key: getattr(settings, key) for key in (
                "panic_distance", "rear_warning_distance", "recent_damage_window_ms",
                "hostile_comment_cooldown_ms", "multi_hostile_comment_cooldown_ms", "panic_scream_cooldown_ms",
                "hostile_mass_callout_threshold", "hostile_query_distance",
                "other_realm_swarm_visual_threshold", "other_realm_audio_generic_threshold")},
            "battle_speed": settings.tts_speed_for_profile("battle"),
            "cue_dir": str(settings.cue_audio_dir.resolve()) if settings.cue_audio_dir is not None else None,
        }),
        "--pitch", str(settings.voicevox_pitch_scale), "--volume", str(settings.voicevox_volume_scale),
        "--max-tokens", str(settings.llm_chat_max_tokens or settings.llm_max_tokens),
        "--timeout-ms", str(int(1000 * (settings.llm_chat_timeout_sec or settings.llm_timeout_sec))),
        "--reading-engine", settings.tts_reading_engine,
        "--audio-dir", str(folder / ".dogido_tmp/rust-dialogue")]
    if settings.voicevox_output_sampling_rate is not None:
        command.extend(["--output-sampling-rate", str(settings.voicevox_output_sampling_rate)])
    if not settings.audio_enabled or settings.tts_backend == "noop":
        command.append("--no-audio")
    if not use_model:
        command.append("--no-llm")
    if not settings.main_language_dialogue_enabled:
        command.append("--no-language")
    os.chdir(ROOT)
    os.execve(binary, command, env)


if __name__ == "__main__":
    try: main()
    except (OSError, ValueError, RuntimeError) as e:
        print(f"起動できません: {e}", file=sys.stderr)
        raise SystemExit(1)
