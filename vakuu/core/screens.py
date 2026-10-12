"""What screen the game is on: fights, scoped takeover screens and the action queue."""
from core.rules import COMBAT

def execution_busy(state):
    execution = state.get('execution')
    if not isinstance(execution, dict): return False  # Older mod compatibility.
    return execution.get('running') is True or execution.get('queue_empty') is False

def combat_choice(state):
    # Paused actions cannot complete until the player answers these prompts.
    return state.get('state_type') in {'hand_select', 'card_select', 'bundle_select', 'relic_select'}

# Screens that belong to one decision (a shop's removal opens card_select, a reward opens card_reward).
SCREEN_GROUPS = {'shop': {'shop', 'card_select'}, 'rewards': {'rewards', 'card_reward', 'card_select'},
                 'card_reward': {'card_reward', 'rewards'}, 'rest_site': {'rest_site', 'card_select'},
                 'event': {'event', 'card_select', 'hand_select'}, 'treasure': {'treasure', 'relic_select'}}

def in_fight(state):
    """A fight screen, including prompts that open inside it (discard/exhaust picks, Hologram's grid)."""
    kind = state.get('state_type')
    player = state.get('player') or {}
    return kind in COMBAT or kind == 'hand_select' or (
        kind in ('card_select', 'bundle_select', 'relic_select') and 'hand' in player and 'energy' in player)

def same_screen(start, now):
    """Whether the decision begun on start is still going (same turn of a fight, same shop...)."""
    kind = start.get('state_type')
    if kind in COMBAT:
        return in_fight(now) and (now.get('battle') or {}).get('round') in (None, (start.get('battle') or {}).get('round'))
    if kind == 'map': return now.get('state_type') == 'map' and (now.get('run') or {}).get('floor') == (start.get('run') or {}).get('floor')
    return now.get('state_type') in SCREEN_GROUPS.get(kind, {kind})

def screen_label(state):
    """What "help me choose" means on this screen, for the panel button."""
    kind = state.get('state_type')
    if kind in COMBAT or kind == 'hand_select': return '幫我打這回合'
    return {'map': '幫我畫路線', 'card_reward': '幫我選牌', 'shop': '幫我逛商店', 'rest_site': '幫我選休息',
            'event': '幫我選事件', 'rewards': '幫我領獎勵', 'card_select': '幫我選牌', 'treasure': '幫我開寶箱'}.get(kind, '幫我選')
