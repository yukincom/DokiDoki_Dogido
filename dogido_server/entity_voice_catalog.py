from __future__ import annotations

from dogido_server.entry_catalog import passive_mob_labels, threat_mob_labels

HOSTILE_MOB_VOICE_LABELS: dict[str, str] = threat_mob_labels()
PASSIVE_MOB_VOICE_LABELS: dict[str, str] = passive_mob_labels()

MOB_VOICE_LABELS: dict[str, str] = {
    **PASSIVE_MOB_VOICE_LABELS,
    **HOSTILE_MOB_VOICE_LABELS,
}

COUNT_FRAGMENT_TEXTS: dict[str, str] = {
    str(number): f"{number}体"
    for number in range(1, 9)
}

PHRASE_FRAGMENT_TEXTS: dict[str, str] = {
    "ga_orude": "がおるで",
    "orude": "おるで",
}

RUNTIME_HOSTILE_LABELS: dict[str, str] = dict(HOSTILE_MOB_VOICE_LABELS)
