"""Rust音声入力へ渡す設定解決。録音・STT・配送・停止はRustが所有する。"""
from pathlib import Path

NORMAL_STT_PROMPT = (
    "Minecraftのプレイ内容について日本語で会話しています。"
    "話題はブロック、アイテム、モブ、バイオーム、建築、戦闘です。"
    "国語や詩について質問することもあり、ワ行イ段、ゐ、枕詞、川柳、"
    "ソネットという語を使います。"
)
HAIKU_WORKSHOP_STT_PROMPT = (
    "Minecraftのプレイ内容について日本語で会話しています。"
    "話題はブロック、アイテム、モブ、バイオーム、建築、戦闘と、"
    "日本語の読み方、言い換え、川柳の上五・中七・下五の推敲です。"
)

WHISPER_CLI_CANDIDATES = (
    Path.home() / "AI_assistant" / "whisper.cpp" / "build" / "bin" / "whisper-cli",
    Path.home() / "whisper.cpp" / "build" / "bin" / "whisper-cli",
)
WHISPER_MODEL_DIRS = (
    Path.home() / "AI_assistant" / "whisper.cpp" / "models",
    Path.home() / "whisper.cpp" / "models",
)
VAD_MODEL_CANDIDATES = (
    Path.home() / ".cache" / "dogido" / "models" / "ggml-silero-v6.2.0.bin",
    *(path / "ggml-silero-v6.2.0.bin" for path in WHISPER_MODEL_DIRS),
)


def resolve_whisper_paths(settings) -> tuple[Path, Path]:
    cli = settings.voice_whisper_cli
    if cli is None:
        cli = next((candidate for candidate in WHISPER_CLI_CANDIDATES if candidate.exists()), None)
    if cli is None or not Path(cli).exists():
        raise SystemExit(
            "whisper-cli が見つかりません。DOGIDO_VOICE_WHISPER_CLI にパスを設定してください。"
        )
    model = settings.voice_whisper_model
    if model is None:
        for models_dir in WHISPER_MODEL_DIRS:
            # 日本語特化の kotoba-whisper を最優先で拾う
            candidates = sorted(models_dir.glob("ggml-kotoba*.bin")) + sorted(
                path for path in models_dir.glob("ggml-*.bin") if ".en" not in path.name
            )
            if candidates:
                model = candidates[0]
                break
    if model is None or not Path(model).exists():
        raise SystemExit(
            "whisper モデル（ggml-*.bin）が見つかりません。DOGIDO_VOICE_WHISPER_MODEL を設定してください。"
        )
    return Path(cli), Path(model)


def resolve_vad_paths(settings, whisper_cli: Path) -> tuple[Path, Path] | None:
    """共有キャッシュか明示設定にSilero VADがあれば、その実行組を返す。"""

    if not settings.voice_vad_enabled:
        return None
    cli = settings.voice_vad_cli or whisper_cli.with_name(
        "whisper-vad-speech-segments"
    )
    model = settings.voice_vad_model
    if model is None:
        model = next((path for path in VAD_MODEL_CANDIDATES if path.exists()), None)
    if model is None or not Path(cli).exists() or not Path(model).exists():
        return None
    return Path(cli), Path(model)
