# catalog_readings.py
"""漢字ラベルの正しい読み（よみ）を管理する。

- カタログ entry の `reading` フィールド
- プレイヤー訂正のオーバーレイ（catalog_corrections.jsonl）

発句はひらがなのみなので、誤読（草地→そうち）をここで抑える。
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("uvicorn.error")

_lock = threading.Lock()
# surface (表示名) -> {reading, forbidden_readings, source}
_overlay: dict[str, dict[str, Any]] = {}
_corrections_path: Path | None = None


def configure_corrections_path(path: Path | None) -> None:
    global _corrections_path
    _corrections_path = path
    reload_overlay()


def reload_overlay() -> None:
    global _overlay
    with _lock:
        loaded: dict[str, dict[str, Any]] = {}
        if _corrections_path is not None and _corrections_path.exists():
            try:
                with _corrections_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            row = json.loads(stripped)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(row, dict):
                            continue
                        surface = str(row.get("surface") or "").strip()
                        reading = str(row.get("reading") or "").strip()
                        if not surface or not reading:
                            continue
                        forbidden = [
                            str(item).strip()
                            for item in (row.get("forbidden_readings") or [])
                            if str(item).strip()
                        ]
                        wrong = str(row.get("wrong_reading") or "").strip()
                        if wrong and wrong not in forbidden:
                            forbidden.append(wrong)
                        loaded[surface] = {
                            "reading": reading,
                            "forbidden_readings": forbidden,
                            "source": row.get("source"),
                        }
            except OSError as exc:
                LOGGER.warning("catalog_readings_overlay_load_failed path=%s detail=%s", _corrections_path, exc)
        _overlay = loaded


def apply_overlay_correction(
    *,
    surface: str,
    reading: str,
    wrong_reading: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """ランタイム辞書を更新（永続化は MemoryStore 側）。"""
    surface = surface.strip()
    reading = reading.strip()
    payload = {
        "reading": reading,
        "forbidden_readings": [wrong_reading] if wrong_reading else [],
        "source": source,
    }
    with _lock:
        existing = dict(_overlay.get(surface) or {})
        forbidden = list(existing.get("forbidden_readings") or [])
        if wrong_reading and wrong_reading not in forbidden:
            forbidden.append(wrong_reading)
        for item in payload["forbidden_readings"]:
            if item not in forbidden:
                forbidden.append(item)
        existing.update({"reading": reading, "forbidden_readings": forbidden, "source": source})
        _overlay[surface] = existing
        return dict(existing)


def clear_overlay_for_tests() -> None:
    global _overlay, _corrections_path
    with _lock:
        _overlay = {}
        _corrections_path = None


def overlay_reading(surface: str | None) -> str | None:
    if not surface:
        return None
    with _lock:
        entry = _overlay.get(surface.strip())
    if not entry:
        return None
    reading = str(entry.get("reading") or "").strip()
    return reading or None


def overlay_forbidden_readings(surface: str | None) -> tuple[str, ...]:
    if not surface:
        return ()
    with _lock:
        entry = _overlay.get(surface.strip())
    if not entry:
        return ()
    values = entry.get("forbidden_readings") or []
    return tuple(str(item) for item in values if item)


def resolve_reading(surface: str | None, catalog_reading: str | None = None) -> str | None:
    """オーバーレイ優先、なければカタログの reading。"""
    overlay = overlay_reading(surface)
    if overlay:
        return overlay
    text = str(catalog_reading or "").strip()
    return text or None


def format_label_with_reading(surface: str | None, catalog_reading: str | None = None) -> str:
    label = str(surface or "").strip()
    if not label:
        return ""
    reading = resolve_reading(label, catalog_reading)
    if reading and reading != label:
        return f"{label}（{reading}）"
    return label


def haiku_reading_terms(
    surfaces: list[str] | tuple[str, ...],
    *,
    catalog_readings: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """発句用: 使ってよい読み / 禁止読み。"""
    catalog_readings = catalog_readings or {}
    allowed: list[str] = []
    forbidden: list[str] = []
    seen_allowed: set[str] = set()
    seen_forbidden: set[str] = set()
    for surface in surfaces:
        label = str(surface or "").strip()
        if not label:
            continue
        reading = resolve_reading(label, catalog_readings.get(label))
        if reading and reading not in seen_allowed:
            seen_allowed.add(reading)
            allowed.append(reading)
        for bad in overlay_forbidden_readings(label):
            if bad and bad not in seen_forbidden and bad != reading:
                seen_forbidden.add(bad)
                forbidden.append(bad)
    return allowed, forbidden
