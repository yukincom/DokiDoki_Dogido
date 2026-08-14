"""呼び名（call_name）→ 名前断片 mp3 の解決。

ハードコードで player_1 固定にしない。
解決順:
  1. player_names/manifest.json の call_name → ファイル名
  2. player_names/{call_name}.mp3（そのまま）
  3. 安全化したファイル名 .mp3
見つからなければ None（呼び出し側は全文 TTS）。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_MANIFEST_NAME = "manifest.json"
_USHIRO_TAIL_REL = "ushiro_tail.mp3"


def player_names_dir(cue_audio_dir: Path | None, override: Path | None = None) -> Path | None:
    if override is not None:
        return override
    if cue_audio_dir is None:
        return None
    return cue_audio_dir / "player_names"


def ushiro_tail_fragment_id() -> str:
    """cue_voice 相対 id（resolve_cue_path 用）。"""
    return f"player_names/{Path(_USHIRO_TAIL_REL).stem}"


def player_name_fragment_id(relative_file: str) -> str:
    """player_names 配下のファイル名 → cue 相対 id。"""
    name = Path(relative_file).name
    stem = Path(name).stem
    return f"player_names/{stem}"


@lru_cache(maxsize=8)
def _load_manifest_map(manifest_path: str) -> dict[str, str]:
    path = Path(manifest_path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("call_name_to_file") or data.get("names") or {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in raw.items():
        k = str(key).strip()
        v = str(value).strip()
        if k and v:
            result[k] = Path(v).name  # ディレクトリは付けない
    return result


def clear_player_name_voice_cache() -> None:
    _load_manifest_map.cache_clear()


def safe_filename_stem(call_name: str) -> str:
    text = (call_name or "").strip()
    if not text:
        return ""
    # ファイル名に使えない文字を除去（日本語はそのまま可）
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", text)
    cleaned = cleaned.strip("._")
    return cleaned[:64]


def resolve_player_name_clip_file(call_name: str, names_dir: Path | None) -> Path | None:
    """call_name に対応する名前 mp3 の絶対 Path。"""
    name = (call_name or "").strip()
    if not name or names_dir is None or not names_dir.is_dir():
        return None

    manifest = _load_manifest_map(str(names_dir / _MANIFEST_NAME))
    mapped = manifest.get(name)
    if mapped:
        candidate = names_dir / Path(mapped).name
        if candidate.is_file():
            return candidate

    direct = names_dir / f"{name}.mp3"
    if direct.is_file():
        return direct

    stem = safe_filename_stem(name)
    if stem:
        slug = names_dir / f"{stem}.mp3"
        if slug.is_file():
            return slug

    return None


def resolve_player_name_fragment_id(call_name: str, names_dir: Path | None) -> str | None:
    """cue_sequence 用の相対 id。解決できなければ None。"""
    path = resolve_player_name_clip_file(call_name, names_dir)
    if path is None:
        return None
    return player_name_fragment_id(path.name)


def ushiro_tail_path(names_dir: Path | None) -> Path | None:
    if names_dir is None:
        return None
    path = names_dir / _USHIRO_TAIL_REL
    return path if path.is_file() else None


def build_named_ushiro_sequence(
    call_name: str,
    *,
    cue_audio_dir: Path | None,
    names_dir: Path | None = None,
) -> list[str] | None:
    """named うしろ: [名前断片, ushiro_tail]。どちらか欠ければ None。"""
    directory = player_names_dir(cue_audio_dir, names_dir)
    name_id = resolve_player_name_fragment_id(call_name, directory)
    if name_id is None:
        return None
    if ushiro_tail_path(directory) is None:
        return None
    return [name_id, ushiro_tail_fragment_id()]
