"""TLS更新の失敗境界を検査。ネットワーク・実環境・モデルを使わない。"""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dogido_server import tls_security as tls

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def versions(monkeypatch, certifi="2026.10.19", pip="26.2.2"):
    def read(name):
        result = {"certifi": certifi, "pip": pip}[name]
        if result is None:
            raise tls.metadata.PackageNotFoundError(name)
        return result
    monkeypatch.setattr(tls.metadata, "version", read)


@pytest.mark.parametrize("certifi,pip,accepted", [
    ("2026.10.19", "26.2.2", True), ("2026.10.20", "26.3", True),
    ("2026.10.19", None, True), ("2026.10.18", "26.2.2", False),
    (None, "26.2.2", False), ("2026.10.19", "26.2.1", False),
    ("2026.10.19rc1", "26.2.2", False), ("2026.10.19", "26.2.2.dev1", False),
])
def test_maintenance_versions(monkeypatch, certifi, pip, accepted):
    versions(monkeypatch, certifi, pip)
    if accepted:
        tls.check_python_tls_packages()
    else:
        with pytest.raises(RuntimeError, match="--bootstrap"):
            tls.check_python_tls_packages()


def test_bootstrap_refuses_global_environment_before_any_process(monkeypatch):
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(RuntimeError, match="venv"):
        tls.bootstrap()


def test_bootstrap_verifies_pinned_artifact_before_index_access(monkeypatch):
    monkeypatch.setattr(sys, "prefix", "/fixture/venv")
    installed = {"pip": "26.1.2", "certifi": "2026.5.20"}
    monkeypatch.setattr(tls.metadata, "version", installed.__getitem__)
    commands = []
    def run(command, **kwargs):
        assert kwargs["check"] is True
        commands.append(command)
        if len(commands) == 1:
            assert {"--no-index", "--no-deps", "--disable-pip-version-check", "--no-cache-dir"} <= set(command)
            assert command[-1].endswith("#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70")
            assert command[-1].startswith("https://files.pythonhosted.org/")
            installed["pip"] = "26.2.2"
        else:
            assert installed["pip"] == "26.2.2"
            assert command[-1] == "certifi>=2026.10.19"
            installed["certifi"] = "2026.10.19"
    monkeypatch.setattr(subprocess, "run", run)
    tls.bootstrap()
    assert len(commands) == 2


def test_bootstrap_keeps_newer_installer(monkeypatch):
    monkeypatch.setattr(sys, "prefix", "/fixture/venv")
    versions(monkeypatch, pip="26.3")
    commands = []
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: commands.append(command))
    tls.bootstrap()
    assert len(commands) == 1 and commands[0][-1] == "certifi>=2026.10.19"


def test_failed_bootstrap_never_reaches_unpinned_install(monkeypatch):
    monkeypatch.setattr(sys, "prefix", "/fixture/venv")
    versions(monkeypatch, pip="26.1.2")
    commands = []
    def fail(command, **kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        tls.bootstrap()
    assert len(commands) == 1 and "--no-index" in commands[0]


def fake_uv(tmp_path, result):
    executable = tmp_path / "uv"
    log = tmp_path / "calls"
    executable.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nprintf "%s\\n" "{result}"\n')
    executable.chmod(0o755)
    return executable, log


@pytest.mark.parametrize("value,accepted", [
    ("uv 0.13.3", False), ("uv 0.13.4", True), ("uv 0.14.0 (abc)", True),
    ("uv 1.0.0", True), ("uv 0.13.4rc1", False), ("unknown", False),
])
def test_uv_preflight_only_reads_version(tmp_path, value, accepted):
    executable, log = fake_uv(tmp_path, value)
    result = subprocess.run(["sh", str(ROOT / "scripts/check_uv.sh"), str(executable)], capture_output=True, text=True)
    assert (result.returncode == 0) is accepted
    assert log.read_text() == "--version\n"


def test_old_uv_rejects_family_bundle_before_creation(tmp_path):
    builder = load("family_tls_builder", "scripts/build_family_client.py")
    executable, log = fake_uv(tmp_path, "uv 0.10.2")
    destination = tmp_path / "bundle"
    with pytest.raises(subprocess.CalledProcessError):
        builder.build(SimpleNamespace(source_root=ROOT, output=destination, uv=executable))
    assert not destination.exists() and not destination.with_suffix(".zip").exists()
    assert log.read_text() == "--version\n"


@pytest.mark.parametrize("layout", ["homebrew", "archive", "wheel"])
def test_family_uv_licenses_follow_the_supplied_installation(tmp_path, layout):
    builder = load("family_license_builder", "scripts/build_family_client.py")
    binary = tmp_path / "bin/uv"
    binary.parent.mkdir()
    binary.write_text("fixture")
    if layout == "wheel":
        site = tmp_path / "lib/python3.11/site-packages"
        info = site / "uv-0.13.5.dist-info"
        directory = info / "licenses"
        directory.mkdir(parents=True)
        (info / "METADATA").write_text("Metadata-Version: 2.1\nName: uv\nVersion: 0.13.5\n")
        (info / "RECORD").write_text("../../../bin/uv,,\nuv-0.13.5.dist-info/licenses/LICENSE-MIT,,\nuv-0.13.5.dist-info/licenses/LICENSE-APACHE,,\n")
    else:
        directory = tmp_path if layout == "homebrew" else binary.parent
    for name in ("LICENSE-MIT", "LICENSE-APACHE"):
        (directory / name).write_text(name)
    assert {p.name for p in builder.uv_license_files(binary, "0.13.5")} == {"LICENSE-MIT", "LICENSE-APACHE"}
    if layout == "wheel":
        with pytest.raises(ValueError, match="LICENSE"):
            builder.uv_license_files(binary, "0.13.4")


def test_generated_colab_uses_verified_installer_and_keeps_model_versions(tmp_path):
    builder = load("tls_colab", "dev_tools/haiku_internal_probe/build_colab_bundle.py")
    bundle = tmp_path / "qat-colab"
    (bundle / "baseline").mkdir(parents=True)
    (bundle / "spec.json").write_text('{}')
    for name in ("colab_probe.py", "qat_jacobian.py"):
        shutil.copyfile(builder.HERE / name, bundle / name)
    for name in ("forward.npz", "jacobian_rows.npy"):
        (bundle / "baseline" / name).write_bytes(b"fixture")
    (bundle / "baseline/report.json").write_text(json.dumps({
        "status": "measurement_complete_review_required", "backend": "metal",
        "spec_sha256": builder.sha(bundle / "spec.json"),
    }))
    builder.package(tmp_path)
    notebook = json.loads((tmp_path / "qat_colab_check.ipynb").read_text())
    cells = [cell["source"] for cell in notebook["cells"] if cell["cell_type"] == "code"]
    for source in cells:
        compile(source, "generated notebook", "exec")
    install = next(source for source in cells if "run_logged('install'" in source)
    assert install.index("run_logged('bootstrap_pip'") < install.index("run_logged('install'")
    assert tls.PIP_BOOTSTRAP_URL in install
    assert "'--no-index'" in install and "'--no-deps'" in install
    assert "[sys.executable, '-S', str(INSTALLER / 'pip')" in install
    assert "certifi>=2026.10.19" in install
    assert "mlx[cuda12]==0.32.0" in install
