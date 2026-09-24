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
    for url in (base, settings.voicevox_url):
        if urlsplit(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("この起動ファイルは既存のlocalhostモデル・VOICEVOX専用です。")
    if settings.voicevox_output_sampling_rate is not None:
        raise ValueError("音声のsampling rate個別設定は未移植です。変更せずに終了します。")
    print(f"Rust版の平時会話試験 / 実行元: {ROOT}", flush=True)
    print(f"設定の読込元: {folder} / モデル: {model}", flush=True)
    print("表示: http://127.0.0.1:5056/rust-chat", flush=True)
    print("会話材料・発話検査はPython補助を利用。通常敵の単体・群れの視認警告・導火開始はRustで処理します。", flush=True)
    print("音だけの敵・ボス固有の反応・戦闘後の復帰・川柳・世界操作は未移植です。", flush=True)
    print("マイクを使う場合は、起動完了後に start_voice.command を開いてください。", flush=True)
    print("終了はこのターミナルで Ctrl+C。共有MLXとVOICEVOX本体は停止しません。", flush=True)
    if args.check:
        from dogido_server.state_machine import DogidoStateMachine
        from dialogue_helper import BridgeLLM
        print("Python補助の依存を確認しました。モデル生成・録音・サーバー起動は行っていません。")
        return
    binary = ROOT / "dogido-rust/target/release/dogido-rust"
    env = dict(os.environ)
    # 認証値は引数や端末表示へ出さない。
    for name, value in {"DOGIDO_AUTH_TOKEN": settings.auth_token,
                        "DOGIDO_LLM_API_KEY": settings.llm_chat_api_key or settings.llm_api_key}.items():
        if value: env[name] = value
        else: env.pop(name, None)
    command = [str(binary), "serve-dialogue", "--listen", "127.0.0.1:5056", "--python", sys.executable,
        "--model", model, "--base-url", base, "--voicevox-url", settings.voicevox_url,
        "--speaker", str(settings.voicevox_speaker), "--speed", str(settings.voicevox_speed_scale_peace),
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
