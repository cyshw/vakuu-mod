"""Auto mode routing: which screens need a stronger model, and when a model failed to decide."""
import json
from combat.facts import combat_facts
from core.rules import COMBAT, validate
from core.screens import fight_key
from deck.advisor import advise


# An elite or boss turn that would cost this share of max HP unblocked goes to the stronger model.
DANGER_SHARE = 0.2

def hard_screen(state):
    """Situations worth a stronger model: bosses, elites, danger, and close card picks."""
    kind = state.get('state_type')
    if kind in ('boss', 'elite'):
        # Sonnet took every turn of these fights (37 of 64 escalations in an Ironclad run). Haiku
        # passes the exam; Sonnet keeps the opening turn (it sets the plan) and the dangerous ones.
        player = state.get('player') or {}
        facts = combat_facts(state)
        return ((state.get('battle') or {}).get('round') or 1) <= 1 or facts['lethal_if_no_more_block'] \
            or facts['block_needed_for_no_damage'] >= DANGER_SHARE * (player.get('max_hp') or 1) \
            or (player.get('hp') or 0) < 0.3 * (player.get('max_hp') or 1)
    # Route choice decides which fights happen at all (an elite at 43% HP lost a run), so it gets
    # the stronger model; single-path maps never reach the model.
    if kind == 'map': return len((state.get('map') or {}).get('next_options') or []) > 1
    # Rest or upgrade is one of the run's biggest choices; Haiku nearly always rested.
    # Shops (remove a Strike or buy?), event choices and deck-changing picks shape the whole run;
    # Haiku bought a card over the first card removal and took a slow card from the opening event.
    if kind in ('shop', 'card_select'): return True
    if kind == 'event': return sum(1 for o in (state.get('event') or {}).get('options') or [] if not o.get('is_locked')) > 1
    if kind == 'rest_site': return sum(1 for o in (state.get('rest_site') or {}).get('options') or [] if o.get('is_enabled', True)) > 1
    if kind in COMBAT:
        player = state.get('player') or {}
        facts = combat_facts(state)
        return facts['lethal_if_no_more_block'] or (player.get('hp') or 0) < 0.3 * (player.get('max_hp') or 1)
    if kind == 'card_reward':
        rating = advise(state)
        rows = rating.get('ranked_cards', [])
        if rating.get('status') != 'ok': return True  # Unrated offers: let the stronger model judge.
        return len(rows) >= 2 and rows[0]['score'] - rows[1]['score'] <= 5
    return False

def undecided(state, decision):
    """A model gave up or answered with a move the screen does not allow."""
    if decision.get('action') == 'none' or decision.get('stop'): return True
    try: validate(state, decision['action'], json.loads(decision['parameters_json']))
    except (ValueError, TypeError): return True
    return False
