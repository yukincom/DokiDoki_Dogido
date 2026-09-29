"""起動境界で実設定を落とさない。モデル・サーバー・録音は起動しない。"""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import launch_dialogue as launch
from dogido_server.config import Settings, get_settings
from dogido_server.language_dialogue import main_web


class Executed(BaseException):
    pass


def command(monkeypatch, tmp_path, settings):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["launch", "--settings-dir", str(tmp_path)])
    monkeypatch.setattr(launch, "get_settings", lambda: settings)
    monkeypatch.setattr(main_web, "inspect_main_web_availability",
                        lambda: SimpleNamespace(available=False, reason="test"))
    captured = {}

    def execute(binary, argv, env):
        captured.update(binary=binary, argv=argv, env=env)
        raise Executed()

    monkeypatch.setattr(os, "execve", execute)
    with pytest.raises(Executed):
        launch.main()
    return captured["argv"], captured["env"]


def value(args, flag):
    return args[args.index(flag) + 1]


def test_optional_cue_disabled_features_and_voice_profiles_reach_rust(monkeypatch, tmp_path):
    settings = Settings(_env_file=None, cue_audio_dir=None, audio_enabled=False,
                        llm_enabled=False, main_language_dialogue_enabled=False,
                        voicevox_speed_scale_peace=.91, voicevox_speed_scale_battle=1.23,
                        voicevox_speed_scale_haiku=.76, voicevox_output_sampling_rate=48000)
    args, _ = command(monkeypatch, tmp_path, settings)
    assert {"--no-audio", "--no-llm", "--no-language"} <= set(args)
    assert value(args, "--speed") == "0.91"
    assert value(args, "--haiku-speed") == "0.76"
    assert value(args, "--output-sampling-rate") == "48000"
    warning = json.loads(value(args, "--warning-settings"))
    assert warning["cue_dir"] is None
    assert warning["battle_speed"] == 1.23
    assert value(args, "--helper") == str(launch.ROOT / "dogido-rust/scripts/dialogue_helper.py")


def test_unset_sampling_and_battle_override_keep_engine_defaults(monkeypatch, tmp_path):
    settings = Settings(_env_file=None, voicevox_speed_scale=1.17,
                        voicevox_speed_scale_battle=None, voicevox_output_sampling_rate=None,
                        audio_enabled=True, llm_enabled=True, main_language_dialogue_enabled=True)
    args, _ = command(monkeypatch, tmp_path, settings)
    assert "--output-sampling-rate" not in args
    assert not {"--no-audio", "--no-llm", "--no-language"} & set(args)
    assert json.loads(value(args, "--warning-settings"))["battle_speed"] == 1.17


def test_shared_profile_is_not_silently_read_as_standalone(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOGIDO_ENV_PROFILE", "shared")
    monkeypatch.setattr(sys, "argv", ["launch", "--settings-dir", str(tmp_path), "--check"])
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="shared runtime profile requires"):
            launch.main()
    finally:
        get_settings.cache_clear()


def test_check_rejects_missing_binary_before_claiming_ready(tmp_path):
    with pytest.raises(RuntimeError, match="Rust本体が未準備"):
        launch.check_runtime_files(tmp_path)


def test_noop_models_do_not_contact_an_api_and_mixed_route_is_explicit(monkeypatch, tmp_path):
    args, _ = command(monkeypatch, tmp_path, Settings(_env_file=None, llm_backend="noop"))
    assert "--no-llm" in args
    assert not json.loads(value(args, "--haiku-settings"))["llm_enabled"]
    with pytest.raises(ValueError, match="片方だけをnoop"):
        launch.model_enabled(Settings(_env_file=None, llm_backend="mlx", llm_chat_backend="noop"))
