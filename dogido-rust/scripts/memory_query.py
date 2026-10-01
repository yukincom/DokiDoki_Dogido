"""Read-only Japanese place/date interpretation; Rust owns all memory I/O."""
from datetime import datetime

from dogido_server.entry_catalog import resolve_biome_place_from_text
from dogido_server.player_input.guardrails import asks_haiku_recall, parse_haiku_time_range
from dogido_server.player_input.normalize import normalize_player_text


def recall_query(text, now=None):
    text = normalize_player_text(text)
    if not asks_haiku_recall(text):
        return None
    place = resolve_biome_place_from_text(text)
    since, until, label = parse_haiku_time_range(text, now=datetime.fromisoformat(now) if now else None)
    return {"biome_id": place.get("biome_id"), "biome_ids": sorted(place.get("biome_ids") or []),
            "place_label": place.get("place_label"), "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None, "time_label": label}
