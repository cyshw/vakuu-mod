"""Card rewards picked the way A10 players pick them: how often each card is taken when offered, by act.

The Codex Score ranks cards by the win rate of runs that hold them, which favours rare cards seen
late in long runs and buries the damage cards every deck takes in act 1 (Twin Strike 21, Breakthrough
14). Run after run took Stomp (23) over Twin Strike or a second Armaments, and reached the act 1 boss
with a starter deck. Choosing by how often players take a card when it is offered matched the A10
player's pick in 49% of act 1 rewards against 39% for the best score (Ironclad, 2133 spots, 5-fold
by run); in acts 2 and 3, where players skip about half the rewards, skipping a card that players
take less than 30% of the time matched 50-58% against 29-33%.

The table is built from Spire Codex runs downloaded with tools/exam/codex_runs.py fetch and written
by `codex_runs.py pickrates`; without it the Codex Score pick stays in use.
"""
import json

from core.paths import RUNTIME

PATH = RUNTIME / 'spire-codex' / 'pick-rates.json'
SKIP_BELOW = 0.3   # From act 2: skip when the most-taken offer is taken less often than this.
SKIP_FROM_ACT = 2
PRIOR = (1, 3)     # Smoothing toward a third: (picks, offers) added to every card.
_table = None


def table():
    global _table
    if _table is None:
        try: _table = json.loads(PATH.read_text(encoding='utf-8'))
        except (OSError, ValueError): _table = {}
    return _table


def rate(counts):
    offered, picked = counts if counts else (0, 0)
    return (picked + PRIOR[0]) / (offered + PRIOR[1])


def popular_pick(state):
    """{'index', 'name', 'pick_rate', 'offered'} for the most-taken offer, {'skip': ...} to skip it, or None."""
    if state.get('state_type') != 'card_reward': return None
    character = str((state.get('player') or {}).get('character_id') or '').split('.')[-1].upper()
    act = (state.get('run') or {}).get('act')
    rows = ((table().get('characters') or {}).get(character) or {}).get(str(act))
    offers = (state.get('card_reward') or {}).get('cards') or []
    if not rows or not offers: return None
    ranked = [{'index': c['index'], 'name': c.get('name'), 'pick_rate': round(rate(rows.get(str(c.get('id') or '').split('.')[-1])), 2),
               'offered': (rows.get(str(c.get('id') or '').split('.')[-1]) or (0, 0))[0]} for c in offers]
    best = max(ranked, key=lambda r: r['pick_rate'])
    if act >= SKIP_FROM_ACT and best['pick_rate'] < SKIP_BELOW and (state.get('card_reward') or {}).get('can_skip', True):
        return {'skip': True, 'best': best}
    return best
