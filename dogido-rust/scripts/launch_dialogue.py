#!/usr/bin/env python3
"""専用ポートの会話試験。既存設定を読むだけで、モデル・OS・Fabric設定を変更しない。"""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server.config import Settings


def voice_settings(settings, folder):
    # 既存AEC環境を再利用。Rust worktreeへ環境を複製・新規導入しない。
    if settings.voice_echo_python is None:
        settings.voice_echo_python = folder / ".dogido_tools/echo-cancel/.venv/bin/python"
    if settings.voice_echo_helper is None:
        settings.voice_echo_helper = folder / ".dogido_tools/echo-cancel/bin/dogido-audio-capture"
    settings.voice_echo_cancellation = "webrtc"
    # Rust会話試験の発話区切り。共有Python版の設定ファイルは変更しない。
    settings.voice_silence_ms = 800
    settings.bind_host, settings.bind_port = "127.0.0.1", 5056
    return settings


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--settings-dir", type=Path, required=True)
    p.add_argument("--voice", action="store_true")
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    folder = args.settings_dir.resolve()
    os.chdir(folder)
    settings = Settings(_env_file=folder / ".env")
    if args.voice:
        settings = voice_settings(settings, folder)
        from dogido_server.voice_capture import echo_command
        from dogido_server.voice_input import (
            resolve_whisper_paths, resolve_vad_paths, NORMAL_STT_PROMPT, HAIKU_WORKSHOP_STT_PROMPT,
        )
        cli, model = resolve_whisper_paths(settings)
        capture_command = echo_command(settings)
        vad = resolve_vad_paths(settings, cli)
        voice_config = {
            "capture_command": capture_command,
            "whisper_cli": str(cli.resolve()), "whisper_model": str(model.resolve()),
            "vad": {"cli": str(vad[0].resolve()), "model": str(vad[1].resolve()),
                    "threshold": settings.voice_vad_threshold} if vad else None,
            "base_url": "http://127.0.0.1:5056",
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
        print(f"Rust音声入力: {model.name} / AECあり / VAD {'あり' if vad else 'なし'}", flush=True)
        print(f"発話区切り: 無音 {settings.voice_silence_ms}ms", flush=True)
        print("配送先: http://127.0.0.1:5056 / 終了はこのターミナルで Ctrl+C", flush=True)
        if not args.check:
            with urllib.request.urlopen("http://127.0.0.1:5056/healthz", timeout=3) as r:
                health = json.load(r)
            if health.get("phase") != "dialogue_preview":
                raise RuntimeError("先に start_dialogue.command でRust版の会話試験を起動してください。")
        binary = ROOT / "dogido-rust/target/release/dogido-rust"
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
    haiku_base = settings.llm_haiku_base_url or settings.llm_base_url or base
    haiku_model = settings.llm_haiku_model or settings.llm_model or model
    haiku_settings = {
        "llm_enabled": settings.llm_enabled, "interval_ms": settings.haiku_interval_ms,
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
        "memory_dir": str(ROOT / ".dogido_memory/rust-migration"),
    }
    for url in (base, haiku_base, settings.voicevox_url):
        if urlsplit(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("この起動ファイルは既存のlocalhostモデル・VOICEVOX専用です。")
    if settings.voicevox_output_sampling_rate is not None:
        raise ValueError("音声のsampling rate個別設定は未移植です。変更せずに終了します。")
    combat_defaults = json.loads((ROOT / "dogido-rust/src/combat/defaults.json").read_text())
    for file in sorted((ROOT / "dogido-rust/src/environment").glob("*_defaults.json")):
        combat_defaults.update(json.loads(file.read_text()))
    combat_settings = {key: getattr(settings, key) for key in combat_defaults}
    from dogido_server.language_dialogue.main_web import inspect_main_web_availability
    web_availability = inspect_main_web_availability()
    web_settings = {"enabled": settings.main_language_web_enabled and settings.main_language_dialogue_enabled,
                    "available": web_availability.available}
    print(f"同意済みWeb検索: {web_availability.reason if web_settings['enabled'] else 'disabled'}（ブラウザー未起動）", flush=True)
    print(f"Rust版の冒険会話試験 / 実行元: {ROOT}", flush=True)
    print(f"設定の読込元: {folder} / モデル: {model}", flush=True)
    print("表示: http://127.0.0.1:5056/rust-chat", flush=True)
    print("会話材料・発話検査はPython補助を利用。戦闘・環境反応の判断、剣への持ち替え、音声配送はRustで処理します。", flush=True)
    print(f"自動川柳: {haiku_model} / 保存先: {haiku_settings['memory_dir']}（セッションごと）", flush=True)
    print("情景発話・発句・保存・掛け軸、句の共同編集・採否・読み訂正・保存した句の検索、知識回答・限定国語対話に対応。Web連携は利用前提が揃うときだけ、同意と案内音声の再生完了後に開始します。", flush=True)
    print("マイクを使う場合は、起動完了後に start_voice.command を開いてください。", flush=True)
    print("終了はこのターミナルで Ctrl+C。共有MLXとVOICEVOX本体は停止しません。", flush=True)
    if args.check:
        from dogido_server.state_machine import DogidoStateMachine
        from dialogue_helper import BridgeLLM
        from haiku_preparation import HaikuPreparation
        from workshop_helper import handle as workshop_handle
        from combat_input_helper import Worker as CombatInputWorker
        print("Python補助の依存を確認しました。モデル生成・録音・サーバー起動は行っていません。")
        return
    binary = ROOT / "dogido-rust/target/release/dogido-rust"
    env = dict(os.environ)
    # 認証値は引数や端末表示へ出さない。
    for name, value in {"DOGIDO_AUTH_TOKEN": settings.auth_token,
                        "DOGIDO_LLM_API_KEY": settings.llm_chat_api_key or settings.llm_api_key,
                        "DOGIDO_LLM_HAIKU_API_KEY": settings.llm_haiku_api_key or settings.llm_api_key}.items():
        if value: env[name] = value
        else: env.pop(name, None)
    command = [str(binary), "serve-dialogue", "--listen", "127.0.0.1:5056", "--python", sys.executable,
        "--model", model, "--base-url", base, "--voicevox-url", settings.voicevox_url,
        "--speaker", str(settings.voicevox_speaker), "--speed", str(settings.voicevox_speed_scale_peace),
        "--combat-settings", json.dumps(combat_settings),
        "--haiku-settings", json.dumps(haiku_settings),
        "--web-settings", json.dumps(web_settings),
        "--warning-settings", json.dumps({
            **{key: getattr(settings, key) for key in (
                "panic_distance", "rear_warning_distance", "recent_damage_window_ms",
                "hostile_comment_cooldown_ms", "multi_hostile_comment_cooldown_ms", "panic_scream_cooldown_ms",
                "hostile_mass_callout_threshold", "hostile_query_distance",
                "other_realm_swarm_visual_threshold", "other_realm_audio_generic_threshold")},
            "battle_speed": settings.voicevox_speed_scale,
            "cue_dir": str(settings.cue_audio_dir.resolve()),
        }),
        "--pitch", str(settings.voicevox_pitch_scale), "--volume", str(settings.voicevox_volume_scale),
        "--max-tokens", str(settings.llm_chat_max_tokens or settings.llm_max_tokens),
        "--timeout-ms", str(int(1000 * (settings.llm_chat_timeout_sec or settings.llm_timeout_sec))),
        "--reading-engine", settings.tts_reading_engine,
        "--audio-dir", str(ROOT / ".dogido_tmp/rust-dialogue")]
    os.chdir(ROOT)
    os.execve(binary, command, env)


if __name__ == "__main__":
    try: main()
    except (OSError, ValueError, RuntimeError) as e:
        print(f"起動できません: {e}", file=sys.stderr)
        raise SystemExit(1)
