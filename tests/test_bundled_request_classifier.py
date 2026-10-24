"""配布する実モデルの完全性と通常導入・家庭用配布の接続を検査する。推論は別途実施。"""
import json
from pathlib import Path
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dogido-rust/scripts"))
import request_classifier_worker as worker
sys.path.insert(0, str(ROOT / "scripts/family-client"))
import family_runtime
sys.path.insert(0, str(ROOT / "scripts"))
import build_family_client


@pytest.mark.parametrize("mode,seed,weight_hash", [
    ("senryu", 42, "2e95a4f304867161adeff507ca5e5e2e89f13df0b7f0ac6bd257538dfc58411d"),
    ("normal", 48, "51c5aaf94c1b6f71cbd38e6d3b57b3aa0bfab0bad2f60408d3e488b2e6481b04"),
])
def test_shipped_checkpoint_matches_adopted_hashes_and_labels(mode, seed, weight_hash):
    model = ROOT / family_runtime.CLASSIFIER_ROOTS[mode]
    manifest = model / "adopted-model.json"
    checkpoint, spec, limits = worker.read_spec({
        "model_root": str(model), "manifest_path": str(manifest),
        "manifest_sha256": worker.digest(manifest), "mode": mode,
    })
    assert checkpoint == model.resolve()
    assert spec["seed"] == seed and spec["classifier_mode"] == mode
    assert spec["checkpoint_files_sha256"]["model.safetensors"] == weight_hash
    assert limits == {"max_length": 128, "max_history_utterances": 0, "truncation": False}
    assert all(not (model / name).is_symlink() for name in worker.REQUIRED_FILES)


def test_new_install_and_family_package_include_the_adopted_models():
    example = (ROOT / ".env.example").read_text()
    assert set(family_runtime.CLASSIFIER_ROOTS) == {"normal", "senryu"}
    for mode, root in family_runtime.CLASSIFIER_ROOTS.items():
        setting = f"DOGIDO_REQUEST_CLASSIFIER_{mode.upper()}_MODEL_ROOT={root}"
        assert setting in example and setting in build_family_client.ENV
    assigned = {line.split("=", 1)[0] for line in example.splitlines()
                if line.strip() and not line.lstrip().startswith("#") and "=" in line}
    assert "DOGIDO_REQUEST_CLASSIFIER_MODEL_ROOT" not in assigned
    assert "DOGIDO_REQUEST_CLASSIFIER_PYTHON=.venv/bin/python" in example
    assert set(family_runtime.CLASSIFIER_FILES) <= set(family_runtime.runtime_files())
    assert len(family_runtime.CLASSIFIER_FILES) == 14
    assert all((ROOT / name).is_file() for name in family_runtime.CLASSIFIER_FILES)
    assert not any("models/dogido-request-v7-seed42/" in name for name in family_runtime.runtime_files())
    defaults = json.loads((ROOT / "dogido_server/runtime_defaults.json").read_text())["request_classifier"]
    assert defaults["normal_model_root"] is None and defaults["senryu_model_root"] is None
    dependencies = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]["request-classifier"]
    assert {name.split("==")[0] for name in dependencies} == {"torch", "transformers", "safetensors"}
