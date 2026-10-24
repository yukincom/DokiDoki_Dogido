"""配布する実モデルの完全性と通常導入・家庭用配布の接続を検査する。推論は別途実施。"""
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dogido-rust/scripts"))
import request_classifier_worker as worker
sys.path.insert(0, str(ROOT / "scripts/family-client"))
import family_runtime


def test_shipped_checkpoint_matches_adopted_hashes_and_labels():
    model = ROOT / family_runtime.CLASSIFIER_ROOT
    manifest = model / "adopted-model.json"
    checkpoint, spec, limits = worker.read_spec({
        "model_root": str(model), "manifest_path": str(manifest),
        "manifest_sha256": worker.digest(manifest),
    })
    assert checkpoint == model.resolve()
    assert spec["seed"] == 42 and spec["data_version"] == "v7-verse-variety-890"
    assert limits == {"max_length": 128, "max_history_utterances": 0, "truncation": False}
    assert all(not (model / name).is_symlink() for name in worker.REQUIRED_FILES)


def test_new_install_and_family_package_include_the_adopted_model():
    example = (ROOT / ".env.example").read_text()
    assert f"DOGIDO_REQUEST_CLASSIFIER_MODEL_ROOT={family_runtime.CLASSIFIER_ROOT}" in example
    assert "DOGIDO_REQUEST_CLASSIFIER_PYTHON=.venv/bin/python" in example
    assert set(family_runtime.CLASSIFIER_FILES) <= set(family_runtime.runtime_files())
    assert all((ROOT / name).is_file() for name in family_runtime.CLASSIFIER_FILES)
    dependencies = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]["request-classifier"]
    assert {name.split("==")[0] for name in dependencies} == {"torch", "transformers", "safetensors"}
