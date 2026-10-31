#!/usr/bin/env python3
"""合成画像をJavaの本体比較・符号化・通信契約へ通す。stdinの要求をJSON eventとして返し、欠けたJDK/Gsonは失敗にする。"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

suite = Path(__file__).resolve().parent
repo = suite.parents[2]
java_home = os.environ.get("JAVA_HOME")
if not java_home and sys.platform == "darwin":
    found = subprocess.run(["/usr/libexec/java_home"], text=True, capture_output=True)
    if found.returncode == 0:
        java_home = found.stdout.strip()
java = str(Path(java_home) / "bin/java") if java_home else shutil.which("java")
javac = str(Path(java_home) / "bin/javac") if java_home else shutil.which("javac")
if not java or not javac or not Path(java).exists() or not Path(javac).exists():
    raise SystemExit("JDK 21以上が必要です。JAVA_HOMEを設定してください。")
gson = os.environ.get("DOGIDO_ACCEPTANCE_GSON")
if not gson:
    cache = Path.home() / ".gradle/caches/modules-2/files-2.1/com.google.code.gson/gson"
    jars = sorted(cache.glob("*/*/gson-*.jar"))
    gson = str(jars[-1]) if jars else None
if not gson or not Path(gson).is_file():
    raise SystemExit("既存Gson jarが必要です。DOGIDO_ACCEPTANCE_GSONで指定してください。")
source = repo / "adapter/minecraft-fabric/src/main/java/dogido/fabric"
with tempfile.TemporaryDirectory(prefix="dogido-image-wire-") as output:
    files = [source / "protocol/DogidoCommandProtocol.java"]
    files += [source / f"screenshot/{name}.java" for name in ("BodyViewProtocol", "BodyViewImage", "BodyViewChange")]
    files += [suite / "java/dogido/fabric/screenshot/BodyViewWireFixture.java"]
    subprocess.run([javac, "--release", "21", "-cp", gson, "-d", output, *map(str, files)], check=True)
    run = subprocess.run([java, "-Djava.awt.headless=true", "-cp", output + os.pathsep + gson,
                          "dogido.fabric.screenshot.BodyViewWireFixture"], input=sys.stdin.buffer.read(), capture_output=True)
    sys.stdout.buffer.write(run.stdout)
    sys.stderr.buffer.write(run.stderr)
    raise SystemExit(run.returncode)
