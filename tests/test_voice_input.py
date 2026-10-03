"""Rustへ渡す音声設定・既存ファイル解決。録音/STT/配送のPython本体は終了。"""
from pathlib import Path
import unittest
from unittest.mock import patch

import pytest

from dogido_server.config import Settings
from dogido_server import voice_settings
from dogido_server.voice_settings import (
    NORMAL_STT_PROMPT, HAIKU_WORKSHOP_STT_PROMPT, resolve_whisper_paths, resolve_vad_paths,
)


class VoiceInputSettingsTests(unittest.TestCase):
    def test_default_capture_window_allows_a_thoughtful_pause(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(1200, settings.voice_silence_ms)
        self.assertEqual(30.0, settings.voice_max_speech_sec)
        self.assertEqual(1, settings.voice_stt_max_pending_segments)
        self.assertEqual(8.0, settings.voice_stt_max_segment_age_sec)

    def test_prompt_is_scoped_to_normal_or_workshop_conversation(self) -> None:
        self.assertEqual(
            NORMAL_STT_PROMPT,
            "Minecraftのプレイ内容について日本語で会話しています。"
            "話題はブロック、アイテム、モブ、バイオーム、建築、戦闘です。"
            "国語や詩について質問することもあり、ワ行イ段、ゐ、枕詞、川柳、"
            "ソネットという語を使います。",
        )
        self.assertEqual(
            HAIKU_WORKSHOP_STT_PROMPT,
            "Minecraftのプレイ内容について日本語で会話しています。"
            "話題はブロック、アイテム、モブ、バイオーム、建築、戦闘と、"
            "日本語の読み方、言い換え、川柳の上五・中七・下五の推敲です。",
        )
        self.assertNotIn("上五", NORMAL_STT_PROMPT)
        self.assertIn("ワ行イ段", NORMAL_STT_PROMPT)
        self.assertIn("上五・中七・下五", HAIKU_WORKSHOP_STT_PROMPT)


def test_explicit_whisper_files_preserve_the_selected_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli, model = Path('tools with spaces/whisper-cli'), Path('models/selected.bin')
    for path in (cli, model):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    settings = Settings(_env_file=None, voice_whisper_cli=cli, voice_whisper_model=model)
    assert resolve_whisper_paths(settings) == (cli, model)


def test_existing_whisper_discovery_prefers_kotoba_without_downloading(tmp_path, monkeypatch):
    cli = tmp_path / 'whisper-cli'
    cli.touch()
    models = tmp_path / 'models'
    models.mkdir()
    for name in ('ggml-base.bin', 'ggml-base.en.bin', 'ggml-kotoba-v2.bin'):
        (models / name).touch()
    monkeypatch.setattr(voice_settings, 'WHISPER_CLI_CANDIDATES', (tmp_path / 'missing', cli))
    monkeypatch.setattr(voice_settings, 'WHISPER_MODEL_DIRS', (models,))
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*'))
    assert resolve_whisper_paths(Settings(_env_file=None)) == (cli, models / 'ggml-kotoba-v2.bin')
    assert before == sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*'))


def test_missing_whisper_files_fail_without_creating_an_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_settings, 'WHISPER_CLI_CANDIDATES', ())
    monkeypatch.setattr(voice_settings, 'WHISPER_MODEL_DIRS', ())
    with pytest.raises(SystemExit, match='whisper-cli'):
        resolve_whisper_paths(Settings(_env_file=None))
    cli = tmp_path / 'whisper-cli'
    cli.touch()
    with pytest.raises(SystemExit, match='モデル'):
        resolve_whisper_paths(Settings(_env_file=None, voice_whisper_cli=cli))
    assert list(tmp_path.iterdir()) == [cli]


def test_disabled_vad_does_not_search_for_files():
    with patch('dogido_server.voice_settings.Path.exists', side_effect=AssertionError('unexpected lookup')):
        assert resolve_vad_paths(Settings(_env_file=None, voice_vad_enabled=False), Path('whisper-cli')) is None


def test_vad_resolves_existing_sibling_and_cache_but_missing_is_optional(tmp_path, monkeypatch):
    cli = tmp_path / 'whisper-cli'
    vad = tmp_path / 'whisper-vad-speech-segments'
    model = tmp_path / 'silero.bin'
    for path in (cli, vad, model):
        path.touch()
    monkeypatch.setattr(voice_settings, 'VAD_MODEL_CANDIDATES', (tmp_path / 'missing', model))
    settings = Settings(_env_file=None, voice_vad_enabled=True)
    assert resolve_vad_paths(settings, cli) == (vad, model)
    settings.voice_vad_model = tmp_path / 'explicit-missing.bin'
    assert resolve_vad_paths(settings, cli) is None
