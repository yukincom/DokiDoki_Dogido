"""Apply a parent-supplied pronunciation snapshot in memory; never read/write disk."""
from dogido_server.catalog_readings import configure_corrections_path, apply_overlay_correction


def apply_reading_snapshot(rows):
    # Each helper is isolated. Clear before applying so tests/reuse cannot carry a
    # previous snapshot into another job. Rust is the only persistence owner.
    configure_corrections_path(None)
    if not isinstance(rows, list):
        raise ValueError("reading corrections must be a list")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("surface"), str) or not isinstance(row.get("reading"), str):
            raise ValueError("invalid reading correction")
        forbidden = list(row.get("forbidden_readings") or [])
        if row.get("wrong_reading") and row["wrong_reading"] not in forbidden:
            forbidden.append(row["wrong_reading"])
        for wrong in forbidden or [None]:
            apply_overlay_correction(surface=row["surface"], reading=row["reading"],
                                     wrong_reading=wrong, source=row.get("source"))
