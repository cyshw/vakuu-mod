"""Auto mode routing: which screens need a stronger model, and when a model failed to decide."""
import json

from sts2_advisor import advise
from vakuu_facts import combat_facts
from vakuu_rules import COMBAT, validate

def hard_screen(state):
    """Situations worth a stronger model: bosses, elites, danger, and close card picks."""
    kind = state.get('state_type')
    if kind in ('boss', 'elite'): return True
    # Route choice decides which fights happen at all (an elite at 43% HP lost a run), so it gets
    # the stronger model; single-path maps never reach the model.
    if kind == 'map': return len((state.get('map') or {}).get('next_options') or []) > 1
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

def fight_key(state):
    """Identifies the current fight (one per floor), or None outside combat."""
    if state.get('state_type') not in COMBAT: return None
    run = state.get('run') or {}
    return (run.get('act'), run.get('floor'))
