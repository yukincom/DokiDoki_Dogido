#!/usr/bin/env python3
"""Build the resident STT helper against the existing local whisper.cpp build.

No model is loaded and no dependency is downloaded. The source and shared library
come from the CMake build that owns --whisper-cli; its runtime paths are preserved.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "dogido-rust/target/release/dogido-whisper-worker"


def local_build(whisper_cli: Path) -> tuple[Path, Path]:
    cli = whisper_cli.expanduser().resolve(strict=True)
    for directory in cli.parents:
        cache = directory / "CMakeCache.txt"
        if cache.is_file():
            match = re.search(r"^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$", cache.read_text(), re.M)
            if match:
                source = Path(match.group(1)).resolve(strict=True)
                if (source / "include/whisper.h").is_file():
                    return cli, source
    raise ValueError("--whisper-cli must belong to an existing local whisper.cpp CMake build")


def mac_linkage(cli: Path) -> tuple[Path, list[Path]]:
    """Resolve the same libwhisper and runtime search paths used by this CLI."""
    load_commands = subprocess.check_output(["otool", "-l", str(cli)], text=True)
    rpaths = []
    for value in re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset", load_commands):
        value = value.replace("@loader_path", str(cli.parent)).replace("@executable_path", str(cli.parent))
        rpaths.append(Path(value).resolve())
    libraries = subprocess.check_output(["otool", "-L", str(cli)], text=True)
    match = re.search(r"^\s*(.+libwhisper[^\n]*\.dylib) \(", libraries, re.M)
    if not match:
        raise ValueError("the selected whisper-cli is not linked to a local libwhisper dylib")
    library = match.group(1)
    if library.startswith("@rpath/"):
        name = library.removeprefix("@rpath/")
        found = next((path / name for path in rpaths if (path / name).is_file()), None)
        if found is None:
            raise ValueError("cannot resolve the selected whisper-cli libwhisper")
        return found.resolve(), rpaths
    library = library.replace("@loader_path", str(cli.parent)).replace("@executable_path", str(cli.parent))
    return Path(library).resolve(strict=True), rpaths


def build(whisper_cli: Path, output: Path = DEFAULT_OUTPUT) -> Path:
    cli, whisper_source = local_build(whisper_cli)
    if sys.platform != "darwin":
        raise ValueError("the local resident Whisper builder currently supports macOS")
    library, rpaths = mac_linkage(cli)
    source = ROOT / "native/whisper_worker/main.cpp"
    includes = [whisper_source / part for part in ("include", "ggml/include", "examples")]
    dependencies = [source, Path(__file__), cli, library,
                    includes[0] / "whisper.h", includes[1] / "ggml.h", includes[2] / "json.hpp"]
    output = output.expanduser().resolve()
    stamp_path = output.with_name(output.name + ".build.json")
    # Paths participate too: selecting a different, older CLI must rebuild.
    stamp = {"inputs": [[str(path), path.stat().st_size, path.stat().st_mtime_ns] for path in dependencies],
             "rpaths": [str(path) for path in rpaths]}
    try:
        previous_stamp = json.loads(stamp_path.read_text())
    except (OSError, ValueError):
        previous_stamp = None
    if output.is_file() and previous_stamp == stamp:
        return output
    compiler = shutil.which("c++")
    if compiler is None:
        raise ValueError("a local C++ compiler is required to build the Whisper helper")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=output.name + ".", dir=output.parent)
    os.close(descriptor)
    try:
        command = [compiler, "-std=c++17", "-O2", "-pthread", "-Wall", "-Wextra", str(source)]
        for include in includes:
            command.extend(["-isystem", str(include)])
        command.append(str(library))
        for rpath in rpaths:
            command.extend(["-Xlinker", "-rpath", "-Xlinker", str(rpath)])
        command.extend(["-o", temporary])
        subprocess.run(command, check=True)
        os.chmod(temporary, 0o755)
        os.replace(temporary, output)
        stamp_path.write_text(json.dumps(stamp) + "\n")
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--whisper-cli", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        print(build(args.whisper_cli, args.output))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"cannot build resident Whisper helper: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
