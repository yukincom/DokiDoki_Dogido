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
        from dogido_server.voice_input import resolve_whisper_paths, resolve_vad_paths, main as voice_main
        cli, model = resolve_whisper_paths(settings)
        echo_command(settings)
        vad = resolve_vad_paths(settings, cli)
        print(f"音声入力: {model.name} / AECあり / VAD {'あり' if vad else 'なし'}", flush=True)
        print(f"発話区切り: 無音 {settings.voice_silence_ms}ms", flush=True)
        print("配送先: http://127.0.0.1:5056 / 終了はこのターミナルで Ctrl+C", flush=True)
        if args.check:
            print("設定・必要ファイルを確認しました。録音・サーバー起動は行っていません。")
            return
        with urllib.request.urlopen("http://127.0.0.1:5056/healthz", timeout=3) as r:
            health = json.load(r)
        if health.get("phase") != "dialogue_preview":
            raise RuntimeError("先に start_dialogue.command でRust版の会話試験を起動してください。")
        # echo_inputの別Pythonも移行中の同じ音声コードを読む。
        os.environ["PYTHONPATH"] = str(ROOT)
        voice_main(settings=settings)
        return
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
    print(f"Rust版の冒険会話試験 / 実行元: {ROOT}", flush=True)
    print(f"設定の読込元: {folder} / モデル: {model}", flush=True)
    print("表示: http://127.0.0.1:5056/rust-chat", flush=True)
    print("会話材料・発話検査はPython補助を利用。戦闘・環境反応の判断、剣への持ち替え、音声配送はRustで処理します。", flush=True)
    print(f"自動川柳: {haiku_model} / 保存先: {haiku_settings['memory_dir']}（セッションごと）", flush=True)
    print("情景発話・発句・保存・掛け軸と、意味相談・読み/音数/出典確認・一行編集・AI修正案の検査・未採用案の採否に対応。想起などは次の移行段階です。", flush=True)
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
