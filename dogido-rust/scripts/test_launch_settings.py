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
    assert value(args, "--helper") == str(launch.ROOT / "dogido-rust/scripts/haiku_tokens.py")


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


def test_configured_address_and_relative_memory_are_preserved(monkeypatch, tmp_path):
    args, _ = command(monkeypatch, tmp_path, Settings(_env_file=None, bind_host="::1",
                        bind_port=5111, memory_dir="records/poems"))
    assert value(args, "--listen") == "[::1]:5111"
    assert json.loads(value(args, "--haiku-settings"))["memory_dir"] == str(tmp_path / "records/poems")
    assert value(args, "--audio-dir") == str(tmp_path / ".dogido_tmp/rust-dialogue")
    assert not (tmp_path / "records").exists()


def test_relative_aec_paths_follow_settings_folder_without_overriding_voice_choices(tmp_path):
    settings = Settings(_env_file=None, voice_echo_python="tools/python", voice_echo_helper="tools/capture",
                        voice_echo_cancellation="off", voice_silence_ms=1450, bind_port=5111)
    resolved = launch.voice_settings(settings, tmp_path)
    assert resolved.voice_echo_python == tmp_path / "tools/python"
    assert resolved.voice_echo_helper == tmp_path / "tools/capture"
    assert (resolved.voice_echo_cancellation, resolved.voice_silence_ms, resolved.bind_port) == ("off",1450,5111)


def test_voice_check_uses_same_endpoint_and_does_not_contact_or_capture(monkeypatch, tmp_path):
    from dogido_server import voice_capture, voice_settings
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["launch", "--settings-dir", str(tmp_path), "--voice", "--check"])
    monkeypatch.setattr(launch, "get_settings", lambda: Settings(_env_file=None, bind_port=5111,
                        voice_silence_ms=1450, voice_echo_cancellation="off"))
    monkeypatch.setattr(voice_capture, "capture_command", lambda _: ["/fixture/capture"])
    monkeypatch.setattr(voice_settings, "resolve_whisper_paths", lambda _: (tmp_path / "whisper", tmp_path / "model"))
    monkeypatch.setattr(voice_settings, "resolve_vad_paths", lambda *_: None)
    def forbidden(*a, **k): raise AssertionError("check must not contact the server")
    monkeypatch.setattr(launch.urllib.request, "urlopen", forbidden)
    captured = {}
    def execute(binary, argv, env):
        captured.update(argv=argv, env=env)
        raise Executed()
    monkeypatch.setattr(os, "execve", execute)
    with pytest.raises(Executed): launch.main()
    args=captured["argv"]
    config=json.loads(value(args, "--settings"))
    assert args[-1] == "--check"
    assert config["base_url"] == "http://127.0.0.1:5111"
    assert config["silence_ms"] == 1450
    assert config["capture_command"] == ["/fixture/capture"]
    assert not list(tmp_path.iterdir())


def test_localhost_is_resolved_without_dns_and_dedicated_wrapper_keeps_checkout_memory(monkeypatch, tmp_path):
    args, _ = command(monkeypatch, tmp_path, Settings(_env_file=None, bind_host="localhost", bind_port=5123))
    assert value(args, "--listen") == "127.0.0.1:5123"
    # A different settings directory must not silently switch dedicated-wrapper records.
    for name in ("start_dialogue.command", "start_voice.command", "start_workshop_text.command"):
        script=(launch.ROOT / "dogido-rust" / name).read_text()
        assert '--memory-dir "$PROJECT_ROOT/.dogido_memory/rust-migration"' in script
        assert '--memory-dir "$CONFIG_ROOT/' not in script


@pytest.mark.parametrize("layout,override", [("long_term", None), ("sessions", "other-records")])
def test_text_workshop_reads_configured_memory_and_resolves_resume_before_chdir(monkeypatch, tmp_path, layout, override):
    import launch_workshop_text as text_launch
    repo = tmp_path / "repo"
    binary = repo / "dogido-rust/target/release/examples/workshop_text"
    binary.parent.mkdir(parents=True)
    binary.touch()
    config = tmp_path / "config"
    config.mkdir()
    memory = config / (override or "poems")
    (memory / layout).mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    argv = ["launch", "--settings-dir", str(config), "--resume", "conversation.json"]
    if override:
        argv += ["--memory-dir", override]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(text_launch, "ROOT", repo)
    monkeypatch.setattr(text_launch, "get_settings", lambda: Settings(_env_file=None, memory_dir="poems"))
    captured = {}
    def execute(binary, argv, env):
        captured.update(argv=argv)
        raise Executed()
    monkeypatch.setattr(os, "execve", execute)
    with pytest.raises(Executed):
        text_launch.main()
    assert value(captured["argv"], "--memory-dir") == str(memory)
    assert value(captured["argv"], "--resume") == str(tmp_path / "conversation.json")
    assert not (repo / ".dogido_memory").exists()
