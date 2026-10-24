"""起動境界で実設定を落とさない。モデル・サーバー・録音は起動しない。"""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import launch_dialogue as launch
from dogido_server.config import Settings, get_settings


class Executed(BaseException):
    pass


def command(monkeypatch, tmp_path, settings):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["launch", "--settings-dir", str(tmp_path)])
    monkeypatch.setattr(launch, "get_settings", lambda: settings)
    monkeypatch.setattr(launch, "check_runtime_files", lambda root: root / "dogido-rust/target/release/dogido-rust")
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


@pytest.mark.parametrize("custom", [None, "custom-game/screenshots"])
def test_screenshot_root_is_optional_and_shared_with_bookshelf(monkeypatch, tmp_path, custom):
    settings = Settings(_env_file=None, minecraft_screenshots_dir=custom)
    args, _ = command(monkeypatch, tmp_path, settings)
    haiku = json.loads(value(args, "--haiku-settings"))
    assert haiku["screenshot_dir"] == (str((tmp_path / custom).resolve()) if custom else None)


def test_internal_capture_is_opt_in_and_preserves_the_shared_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("DOGIDO_CAPTURE_DIR", raising=False)
    monkeypatch.delenv("DOGIDO_HAIKU_INTERNAL_CAPTURE_ENABLED", raising=False)
    args, _ = command(monkeypatch, tmp_path, Settings(_env_file=None))
    haiku = json.loads(value(args, "--haiku-settings"))
    assert not haiku["internal_capture_enabled"] and haiku["capture_dir"] is None
    monkeypatch.setenv("DOGIDO_HAIKU_INTERNAL_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("DOGIDO_CAPTURE_DIR", "captures/raw")
    args, _ = command(monkeypatch, tmp_path, Settings(_env_file=None))
    haiku = json.loads(value(args, "--haiku-settings"))
    assert haiku["internal_capture_enabled"] is True
    assert haiku["capture_dir"] == str(tmp_path / "captures/raw")
    assert not (tmp_path / "captures").exists()


def test_internal_capture_enabled_without_directory_is_an_explicit_error(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="DOGIDO_CAPTURE_DIR"):
        command(monkeypatch, tmp_path, Settings(_env_file=None,
                haiku_internal_capture_enabled=True, capture_dir=None))


def test_optional_cue_disabled_features_and_voice_profiles_reach_rust(monkeypatch, tmp_path):
    settings = Settings(_env_file=None, cue_audio_dir=None, audio_enabled=False,
                        llm_enabled=False,
                        voicevox_speed_scale_peace=.91, voicevox_speed_scale_battle=1.23,
                        voicevox_speed_scale_haiku=.76, voicevox_output_sampling_rate=48000)
    args, _ = command(monkeypatch, tmp_path, settings)
    assert {"--no-audio", "--no-llm"} <= set(args)
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
                        audio_enabled=True, llm_enabled=True)
    args, _ = command(monkeypatch, tmp_path, settings)
    assert "--output-sampling-rate" not in args
    assert not {"--no-audio", "--no-llm"} & set(args)
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
        launch.model_enabled(Settings(_env_file=None, llm_backend="chat_completions", llm_chat_backend="noop"))


def test_removed_say_backend_cannot_silently_select_voicevox():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(_env_file=None, tts_backend="say")


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
    monkeypatch.setattr(launch, "check_runtime_files", lambda root: root / "dogido-rust/target/release/dogido-rust")
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


def test_localhost_is_resolved_without_dns(monkeypatch, tmp_path):
    args, _ = command(monkeypatch, tmp_path, Settings(_env_file=None, bind_host="localhost", bind_port=5123))
    assert value(args, "--listen") == "127.0.0.1:5123"


@pytest.mark.skipif(shutil.which("zsh") is None, reason="起動ファイルの検証にはzshが必要")
@pytest.mark.parametrize("name,launcher,voice", [
    ("start_dialogue.command", "launch_dialogue.py", False),
    ("start_voice.command", "launch_dialogue.py", True),
    ("start_workshop_text.command", "launch_workshop_text.py", False),
])
@pytest.mark.parametrize("check", [False, True])
def test_wrappers_forward_shared_settings_without_port_or_memory_overrides(tmp_path, name, launcher, voice, check):
    # 設定フォルダをcheckoutの外へ置き、通常起動と事前確認の両方を通す。
    # interpreterとbuildだけ代替し、モデル・本体・SDK・録音は起動しない。
    scripts = tmp_path / "checkout" / "dogido-rust"
    scripts.mkdir(parents=True)
    wrapper = scripts / name
    wrapper.write_bytes((launch.ROOT / "dogido-rust" / name).read_bytes())
    cargo = scripts / "cargo.sh"
    cargo.write_text("#!/bin/sh\nexit 0\n")
    cargo.chmod(0o700)
    interpreter = tmp_path / "capture interpreter"
    interpreter.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable)
                           + " -c 'import json, sys; print(json.dumps(sys.argv[1:]))' \"$@\"\n")
    interpreter.chmod(0o700)
    config = tmp_path / "selected settings"
    config.mkdir()
    env = dict(os.environ, DOGIDO_PYTHON=str(interpreter), DOGIDO_RUST_SETTINGS_DIR=str(config),
               DOGIDO_RUST_USE_PREBUILT="0", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(["zsh", str(wrapper), *(["--check"] if check else [])],
                            env=env, cwd=tmp_path, capture_output=True, text=True, check=True, timeout=10)
    args = json.loads(result.stdout)
    assert Path(args[0]).name == launcher
    assert value(args, "--settings-dir") == str(config)
    assert "--port" not in args and "--memory-dir" not in args
    assert ("--voice" in args) is voice
    assert ("--check" in args) is check
    if voice:
        assert "--aec" in args and value(args, "--silence-ms") == "800"


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



def test_shared_defaults_and_env_overrides_reach_both_rust_consumers(monkeypatch, tmp_path):
    from dogido_server.runtime_settings import COMBAT_DEFAULTS, SERVER_DEFAULTS
    monkeypatch.chdir(tmp_path)
    # Exercise the actual environment boundary, including a float and both HTTP limits.
    (tmp_path / ".env").write_text("DOGIDO_PANIC_DISTANCE=9.25\nDOGIDO_MAX_BATCH_SIZE=3\n"
                                   "DOGIDO_MAX_BODY_KB=7\nDOGIDO_HEARTBEAT_INTERVAL_MS=900\n"
                                   "DOGIDO_ACCEPTED_SCHEMA_VERSION=fixture-version\n")
    settings = Settings()
    args, _ = command(monkeypatch, tmp_path, settings)
    combat = json.loads(value(args, "--combat-settings"))
    warning = json.loads(value(args, "--warning-settings"))
    server = json.loads(value(args, "--server-settings"))
    assert combat["panic_distance"] == warning["panic_distance"] == 9.25
    assert server == {"max_batch_size": 3, "max_body_kb": 7, "heartbeat_interval_ms": 900,
                      "accepted_schema_version": "fixture-version"}
    assert combat.keys() == COMBAT_DEFAULTS.keys()
    for key, expected in (COMBAT_DEFAULTS | SERVER_DEFAULTS).items():
        assert Settings.model_fields[key].default == expected, key


def test_explicit_vad_path_from_env_reaches_rust_voice_settings(monkeypatch, tmp_path):
    from dogido_server import voice_capture
    monkeypatch.chdir(tmp_path)
    for name in ("whisper", "model.bin", "chosen-vad", "vad.bin"):
        (tmp_path / name).touch()
    (tmp_path / ".env").write_text("DOGIDO_VOICE_WHISPER_CLI=whisper\n"
        "DOGIDO_VOICE_WHISPER_MODEL=model.bin\nDOGIDO_VOICE_VAD_ENABLED=true\n"
        "DOGIDO_VOICE_VAD_CLI=chosen-vad\nDOGIDO_VOICE_VAD_MODEL=vad.bin\n"
        "DOGIDO_VOICE_VAD_THRESHOLD=0.67\n")
    monkeypatch.setattr(sys, "argv", ["launch", "--settings-dir", str(tmp_path), "--voice", "--check"])
    monkeypatch.setattr(launch, "get_settings", lambda: Settings())
    monkeypatch.setattr(launch, "check_runtime_files", lambda root: root / "dogido-rust/target/release/dogido-rust")
    monkeypatch.setattr(voice_capture, "capture_command", lambda _: ["/fixture/capture"])
    def forbidden(*a, **k): raise AssertionError("voice check must not contact any server")
    monkeypatch.setattr(launch.urllib.request, "urlopen", forbidden)
    captured = {}
    def execute(binary, argv, env):
        captured["settings"] = json.loads(value(argv, "--settings"))
        raise Executed()
    monkeypatch.setattr(os, "execve", execute)
    with pytest.raises(Executed):
        launch.main()
    assert captured["settings"]["vad"] == {"cli": str(tmp_path / "chosen-vad"),
        "model": str(tmp_path / "vad.bin"), "threshold": 0.67}


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.10"])
def test_non_loopback_is_rejected_before_launch(monkeypatch, tmp_path, host):
    with pytest.raises(ValueError, match="loopback専用"):
        command(monkeypatch, tmp_path, Settings(_env_file=None, bind_host=host))


def test_retired_env_settings_warn_by_name_without_values(monkeypatch, tmp_path):
    from dogido_server.runtime_settings import warn_retired_settings
    monkeypatch.setattr(os, "environ", {})
    env = tmp_path / ".env"
    env.write_text("DOGIDO_SAY_VOICE=PRIVATE_VALUE\nDOGIDO_ALLOW_NON_LOCAL_BIND=true\n"
                   "DOGIDO_VOICE_VAD_CLI=still-used\nDOGIDO_AUTH_TOKEN=SECRET\n")
    with pytest.warns(UserWarning) as records:
        warn_retired_settings((env,))
    message = str(records[0].message)
    assert "DOGIDO_SAY_VOICE" in message and "DOGIDO_ALLOW_NON_LOCAL_BIND" in message
    assert all(word not in message for word in ("PRIVATE_VALUE", "SECRET", "DOGIDO_AUTH_TOKEN", "DOGIDO_VOICE_VAD_CLI"))


def test_settings_package_does_not_need_rust_source_tree(tmp_path):
    import shutil
    import subprocess
    package = tmp_path / "dogido_server"
    package.mkdir()
    for name in ("__init__.py", "config.py", "runtime_settings.py", "runtime_defaults.json"):
        shutil.copyfile(launch.ROOT / "dogido_server" / name, package / name)
    run = subprocess.run([sys.executable, "-c",
        "from dogido_server.config import Settings; "
        "from dogido_server.runtime_settings import COMBAT_DEFAULTS; "
        "s=Settings(_env_file=None); assert s.panic_distance==COMBAT_DEFAULTS['panic_distance']"],
        cwd=tmp_path, env={"PATH": os.environ.get("PATH", "")}, capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr


def test_source_and_embedded_assets_require_a_newer_binary(tmp_path):
    source = tmp_path / "dogido-rust/src"
    source.mkdir(parents=True)
    main = source / "main.rs"
    main.write_text('const PAGE: &str = include_str!("page.html");')
    page = source / "page with spaces.html"
    page.write_text("page")
    binary = tmp_path / "dogido-rust/target/release/dogido-rust"
    binary.parent.mkdir(parents=True)
    binary.touch()
    encoded_page = str(page).replace(" ", "\\ ")
    binary.with_suffix(".d").write_text(f"{binary}: {main} {encoded_page}\n")
    # cargo build --release does not compile a cfg(test)-only module.
    test_module = source / "tests.rs"
    test_module.write_text("test only")
    os.utime(test_module, ns=(99, 99))
    for path in (main, page): os.utime(path, ns=(10, 10))
    os.utime(binary, ns=(20, 20))
    launch.check_binary_freshness(tmp_path, binary)
    os.utime(page, ns=(30, 30))
    with pytest.raises(RuntimeError, match="page with spaces.html"):
        launch.check_binary_freshness(tmp_path, binary)
    os.utime(page, ns=(10, 10))
    os.utime(main, ns=(30, 30))
    with pytest.raises(RuntimeError, match="main.rs"):
        launch.check_binary_freshness(tmp_path, binary)


def test_family_bundle_without_local_rust_source_skips_source_freshness(tmp_path):
    binary = tmp_path / "dogido-rust/target/release/dogido-rust"
    binary.parent.mkdir(parents=True)
    binary.touch()
    launch.check_binary_freshness(tmp_path, binary)



def test_source_checkout_requires_cargo_dependency_record(tmp_path):
    source = tmp_path / "dogido-rust/src"
    source.mkdir(parents=True)
    (source / "main.rs").touch()
    binary = tmp_path / "dogido-rust/target/release/dogido-rust"
    binary.parent.mkdir(parents=True)
    binary.touch()
    with pytest.raises(RuntimeError, match="ビルド依存情報がありません"):
        launch.check_binary_freshness(tmp_path, binary)


def test_request_classifier_config_reaches_shared_launcher(monkeypatch, tmp_path):
    import request_classifier_worker as worker
    model = tmp_path / "classifier-model"
    model.mkdir()
    (model / "adopted-model.json").write_text('{}')
    python = tmp_path / ".dogido_tools/request_classifier/.venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    python.chmod(0o700)
    checked = []
    monkeypatch.setattr(worker, "read_spec", lambda args: checked.append(args))
    settings = Settings(_env_file=None, request_classifier_model_root="classifier-model",
                        request_classifier_load_timeout_ms=32100, request_classifier_timeout_ms=1234)
    args, _ = command(monkeypatch, tmp_path, settings)
    result = json.loads(value(args, "--request-classifier-settings"))
    assert result == {"model_root": str(model), "python": str(python),
                      "load_timeout_ms":32100, "timeout_ms":1234}
    assert checked[0]["manifest_path"] == str(model / "adopted-model.json")
    assert len(checked[0]["manifest_sha256"]) == 64


def test_request_classifier_disabled_and_invalid_selection(monkeypatch, tmp_path):
    args, _ = command(monkeypatch, tmp_path, Settings(_env_file=None))
    assert json.loads(value(args, "--request-classifier-settings"))["model_root"] is None
    with pytest.raises(RuntimeError, match="Python"):
        launch.request_classifier_settings(Settings(_env_file=None, request_classifier_model_root="missing"), tmp_path)


def test_chat_image_launch_settings_and_bounds(monkeypatch, tmp_path):
    from pydantic import ValidationError
    from dogido_server.runtime_settings import DEFAULTS
    settings = Settings(_env_file=None, chat_image_enabled=True, chat_image_wait_ms=1900, chat_image_stop_reactions_enabled=False)
    argv, _ = command(monkeypatch, tmp_path, settings)
    assert json.loads(argv[argv.index("--chat-image-settings") + 1]) == {"enabled": True, "wait_ms": 1900, "stop_reactions_enabled": False}
    defaults = Settings(_env_file=None)
    assert defaults.chat_image_enabled == DEFAULTS["chat_image"]["enabled"]
    assert defaults.chat_image_wait_ms == DEFAULTS["chat_image"]["wait_ms"]
    for invalid in (0, 99, 5001):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, chat_image_wait_ms=invalid)


def test_os_ai_and_provisional_resume_settings_are_removed(monkeypatch, tmp_path):
    settings = Settings(_env_file=None)
    assert not any(name.startswith("platform_ai_") for name in Settings.model_fields)
    assert "workshop_low_threat_resume_delay_ms" not in Settings.model_fields
    args, _ = command(monkeypatch, tmp_path, settings)
    haiku = json.loads(value(args, "--haiku-settings"))
    assert "platform_ai" not in haiku and "low_threat_resume_delay_ms" not in haiku


def test_retired_os_ai_settings_only_warn_by_name(monkeypatch, tmp_path):
    from dogido_server.runtime_settings import warn_retired_settings
    monkeypatch.setattr(os, "environ", {})
    env = tmp_path / ".env"
    env.write_text("DOGIDO_PLATFORM_AI_PROVIDER=PRIVATE_PROVIDER\n"
                   "DOGIDO_PLATFORM_AI_ALLOW_MODEL_DOWNLOAD=PRIVATE_VALUE\n"
                   "DOGIDO_WORKSHOP_LOW_THREAT_RESUME_DELAY_MS=12345\n")
    with pytest.warns(UserWarning) as records:
        warn_retired_settings((env,))
    message = str(records[0].message)
    assert "DOGIDO_PLATFORM_AI_PROVIDER" in message
    assert "DOGIDO_PLATFORM_AI_ALLOW_MODEL_DOWNLOAD" in message
    assert "DOGIDO_WORKSHOP_LOW_THREAT_RESUME_DELAY_MS" in message
    assert all(value not in message for value in ("PRIVATE_PROVIDER", "PRIVATE_VALUE", "12345"))
