"""現行HTTP試験のimport境界を検査する。service・モデル・音声は起動しない。"""
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", [
    "check_memory_runtime",
    "check_knowledge_handoff",
    "check_workshop_praise",
])
def test_http_check_imports_in_fresh_process(module, tmp_path):
    # 各試験を独立したPythonで読み、先行テストのsys.modulesによる欠損隠しを避ける。
    scripts = Path(__file__).resolve().parent
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import importlib, sys; sys.path.insert(0, sys.argv[1]); "
         "module = importlib.import_module(sys.argv[2]); assert callable(module.main)",
         str(scripts), module],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"{module}:\n{result.stdout}\n{result.stderr}"
