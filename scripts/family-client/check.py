"""録音・音声再生・推論・サーバー起動をしない導入確認。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CheckError(RuntimeError):
    pass


def get_json(url: str, label: str) -> dict | list | str:
    try:
        with build_opener(ProxyHandler({})).open(url, timeout=5) as response:
            value = json.load(response)
        return value
    except (URLError, OSError, ValueError) as error:
        raise CheckError(f"応答を取得できません: {label} ({url})\n{error}") from error


def check_endpoint(url: str, label: str, valid) -> None:
    result = get_json(url, label)
    if not valid(result):
        raise CheckError(f"応答の内容が想定と異なります: {label} ({url})")
    print(f"OK: {label}")


def check_network(voicevox_url: str) -> None:
    endpoints = (
        ("http://127.0.0.1:8080/v1/models", "共有LLM API（01のSSH接続後に確認）",
         lambda result: isinstance(result, dict) and isinstance(result.get("data"), list)),
        (voicevox_url + "/version", "このMacのVOICEVOX API",
         lambda result: isinstance(result, str) and bool(result.strip())),
    )
    failures = []
    for url, label, valid in endpoints:
        try:
            check_endpoint(url, label, valid)
        except CheckError as error:
            failures.append(str(error))
    if failures:
        raise SystemExit("\n".join(failures))
    # /v1/models lists models; it does not prove which one is loaded or inference success.
    print("API応答の確認が完了しました。モデルの実生成・実際の音声再生はまだ確認していません。")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--server", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    os.environ["DOGIDO_ENV_PROFILE"] = "shared"
    from dogido_server.config import get_settings
    settings = get_settings()  # shared profile validation forbids a local MLX fallback.
    from family_runtime import validate_runtime, server_url, is_rust_ready, launch_command
    validate_runtime(ROOT)
    base_url = server_url(settings)
    if args.network:
        check_network(settings.voicevox_url)
        return
    if args.server:
        check_endpoint(base_url + "/healthz", "このMacのRustドギド（02を先に起動）",
                       is_rust_ready)
        return
    from dogido_server.voice_settings import resolve_whisper_paths, resolve_vad_paths
    from dogido_server.voice_capture import echo_command
    subprocess.run(launch_command(ROOT, check=True), check=True)
    subprocess.run(launch_command(ROOT, voice=True, check=True), check=True)
    import fugashi
    fugashi.Tagger()
    whisper, model = resolve_whisper_paths(settings)
    vad = resolve_vad_paths(settings, whisper)
    if vad is None:
        raise SystemExit("Silero VAD が不足しています")
    for program in (whisper, vad[0]):
        subprocess.run([str(program.resolve()), "--help"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    command = echo_command(settings)
    subprocess.run(command + ["--check"], check=True)
    print(f"OK: Rust本体・接続補助・読み辞書・音声認識・AEC（録音なし、モデル {model.name}）")
    print(f"本体: {base_url} / 記憶: {settings.memory_dir}")
    print("Qwen は親Mac、会話処理・VOICEVOX・再生はこのMacです。")


if __name__ == "__main__":
    try:
        main()
    except (CheckError, ValueError) as error:
        raise SystemExit(str(error)) from error
