"""Closed, read-only projection for the native player_chat prompt builder."""
TEXT_FIELDS = (
    'user_text', 'player_name', 'character_mode', 'mode', 'reply_stance', 'reply_policy',
    'threat_summary', 'biome', 'place_context', 'structure_label', 'time_phase', 'weather',
    'weather_label', 'weather_fact', 'weather_context', 'player_turn_plan',
    'player_turn_plan_evidence', 'safety_priority', 'home_progress', 'inventory_summary',
    'held_item_label', 'hearing_summary', 'observation_summary', 'look_target_label',
    'catalog_topic_hints', 'plausibility_hints', 'conversation_history', 'event_digest',
    'player_chat_plan_action', 'entity_query', 'entity_grounding_status',
    'haiku_workshop_open', 'haiku_workshop_text', 'haiku_workshop_materials',
)
BOOL_FIELDS = ('combat_active', 'has_visual_threats', 'danger_darkness_high', 'asks_inventory',
               'include_sky_context', 'world_observation_available')
LIST_FIELDS = ('hearing_named_mobs', 'hearing_source_labels', 'entity_candidate_labels',
               'entity_observed_labels', 'nearby_hostile_types', 'mob_tactics_notes', 'safe_hints')
OBJECT_FIELDS = ('conversation_repair', 'player_chat_repair')
FIELDS = frozenset((*TEXT_FIELDS, *BOOL_FIELDS, *LIST_FIELDS, *OBJECT_FIELDS))


def prompt_input(request, model, max_tokens):
    if request.kind != 'player_chat':
        raise ValueError('native chat prompt only accepts player_chat')
    return {'schema_version': 1, 'kind': request.kind, 'model': model,
            'details': {k: v for k, v in request.details.items() if k in FIELDS},
            'temperature': request.temperature,
            'max_tokens': request.max_tokens or max_tokens, 'enable_thinking': False}
