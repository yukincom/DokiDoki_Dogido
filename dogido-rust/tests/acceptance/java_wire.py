#!/usr/bin/env python3
"""見込み: 本体Javaの比較結果を通信JSONへ渡せばRustと同じ三状態を保てる。合格: fixtureが未比較・無変化・変化と4枚前参照を検査してeventを返す。範囲: 合成・保存画像と本体Java。GPU、HTTP、モデルは未確認。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

SUITE = Path(__file__).resolve().parent
TEST_REPO = SUITE.parents[2]
REPO = Path(os.environ.get("DOGIDO_ACCEPTANCE_REPO", TEST_REPO)).resolve()
WIRE_PRODUCTION = ["protocol/DogidoCommandProtocol.java",
    *[f"screenshot/{name}.java" for name in ("BodyViewProtocol", "BodyViewImage", "BodyViewChange", "BodyViewBox")]]


def java_tools():
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
    return java, javac, gson


def run_fixture(name, production_sources, *, input_bytes=b"", args=(), extra_sources=(), extra_classpath=()):
    """本体ソースを読取り、専用一時ディレクトリでコンパイルする。依存不足やJava失敗を成功・skipへ変換しない。"""
    started = time.perf_counter()
    java, javac, gson = java_tools()
    source = REPO / "adapter/minecraft-fabric/src/main/java/dogido/fabric"
    files = [source / path for path in production_sources]
    files += [SUITE / f"java/dogido/fabric/screenshot/{name}.java", *extra_sources]
    classpath = os.pathsep.join([gson, *map(str, extra_classpath)])
    with tempfile.TemporaryDirectory(prefix="dogido-image-test-") as output:
        compile_started = time.perf_counter()
        subprocess.run([javac, "--release", "21", "-encoding", "UTF-8", "-cp", classpath, "-d", output, *map(str, files)], check=True)
        run_started = time.perf_counter()
        result = subprocess.run([java, "-Djava.awt.headless=true", "-cp", output + os.pathsep + classpath,
            "dogido.fabric.screenshot." + name, *map(str, args)], input=input_bytes, capture_output=True)
        finished = time.perf_counter()
        # 計測だけをstderrへ出す。画像JSON・時刻・期限・成功条件は変更しない。
        if os.environ.get("DOGIDO_ACCEPTANCE_TIMING") == "1":
            request = json.loads(input_bytes) if name == "BodyViewWireFixture" else {}
            print("IMAGE_TIMING " + json.dumps({
                "stage": "java_fixture", "fixture": name,
                "scenario": request.get("scenario"),
                "image_kind": "stored_png" if "before_path" in request else "synthetic",
                "prepare_ms": round((compile_started - started) * 1000, 3),
                "javac_ms": round((run_started - compile_started) * 1000, 3),
                "java_ms": round((finished - run_started) * 1000, 3),
                "total_ms": round((finished - started) * 1000, 3),
                "exit_code": result.returncode,
            }), file=sys.stderr, flush=True)
        return result


def main():
    result = run_fixture("BodyViewWireFixture", WIRE_PRODUCTION,
        input_bytes=sys.stdin.buffer.read())
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
