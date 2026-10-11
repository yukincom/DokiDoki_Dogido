"""Exercise the manager with an isolated mock; never start the real services."""
from pathlib import Path
import subprocess
import tempfile

source = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="dogido-manager-tests-") as temporary:
    work = Path(temporary)
    runner = work / "manager-tests"
    mock = work / "mock-server"
    subprocess.run([
        "xcrun", "swiftc", "-O", "-module-cache-path", str(work / "swift-cache"),
        str(source / "Sources/ServiceController.swift"), str(source / "Tests/main.swift"),
        "-o", str(runner),
    ], check=True)
    subprocess.run([
        "xcrun", "clang", "-Wall", "-Wextra", "-Werror",
        str(source / "Tests/mock_server.c"), "-o", str(mock),
    ], check=True)
    subprocess.run([str(runner), "--integration", str(mock)], check=True)
