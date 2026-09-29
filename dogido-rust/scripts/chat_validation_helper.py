"""Read-only projection of a player_chat leaf into Rust-owned validation/retry."""
from chat_prompt_helper import prompt_input

VALIDATION_FIELDS = frozenset((
    'player_name', 'mode', 'character_mode', 'has_visual_threats', 'combat_active',
    'nearby_hostile_types', 'nearby_mob_ids', 'threat_summary', 'hearing_summary',
    'event_digest', 'forbidden_advice', 'speech_whitelist_enforce',
    'allowed_speech_labels', 'speech_name_corrections', 'user_text',
    'player_turn_plan', 'safety_priority',
))


def leaf_input(request, model, max_tokens):
    return {'prompt': prompt_input(request, model, max_tokens),
            'validation': {k:v for k,v in request.details.items() if k in VALIDATION_FIELDS},
            'fallback_text': request.fallback_text}
