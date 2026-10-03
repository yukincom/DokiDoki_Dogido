from pathlib import Path
import importlib.util
import json

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/family-client'
spec = importlib.util.spec_from_file_location('family_fabric', SOURCE / 'install_fabric.py')
fabric = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fabric)


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / 'kit'
    bundled = root / 'fabric-version' / fabric.VERSION
    bundled.mkdir(parents=True)
    profile = {'id': fabric.VERSION, 'inheritsFrom': '1.21.11',
               'mainClass': 'fabric.Loader', 'libraries': [{'name': 'fabric:loader:0.18.4'}],
               'time': 'new', 'releaseTime': 'new'}
    (bundled / (fabric.VERSION + '.json')).write_text(json.dumps(profile))
    (bundled / (fabric.VERSION + '.jar')).write_bytes(b'')
    minecraft = tmp_path / 'Minecraft'
    minecraft.mkdir()
    return root, minecraft, profile


def test_installer_preserves_same_fabric_with_different_dates_and_full_jar(layout):
    root, minecraft, profile = layout
    dest = minecraft / 'versions' / fabric.VERSION
    dest.mkdir(parents=True)
    profile.update(time='old', releaseTime='old')
    original = json.dumps(profile).encode()
    (dest / (fabric.VERSION + '.json')).write_bytes(original)
    (dest / (fabric.VERSION + '.jar')).write_bytes(b'EXISTING FULL GAME JAR')
    fabric.install(root, minecraft)
    fabric.install(root, minecraft)
    assert (dest / (fabric.VERSION + '.json')).read_bytes() == original
    assert (dest / (fabric.VERSION + '.jar')).read_bytes() == b'EXISTING FULL GAME JAR'
    assert str(root / 'minecraft') in (root / 'Minecraft設定.txt').read_text()


def test_conflicting_profile_is_preserved_and_instructions_still_exist(layout):
    root, minecraft, profile = layout
    dest = minecraft / 'versions' / fabric.VERSION
    dest.mkdir(parents=True)
    profile['mainClass'] = 'custom.Main'
    original = json.dumps(profile)
    target = dest / (fabric.VERSION + '.json')
    target.write_text(original)
    with pytest.raises(ValueError, match='異なる起動設定'):
        fabric.install(root, minecraft)
    assert target.read_text() == original
    assert (root / 'Minecraft設定.txt').exists()


def test_missing_minecraft_still_leaves_manual_instructions(layout):
    root, minecraft, _ = layout
    with pytest.raises(ValueError):
        fabric.install(root, minecraft / 'missing')
    assert (root / 'Minecraft設定.txt').exists()
