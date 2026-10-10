"""Audit candidate-only eligibility at actual model decision points in takeover logs."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vakuu'))
from vakuu_choices import choice_offer
from vakuu_search import search_turn
from vakuu_rules import COMBAT
from vakuu_facts import combat_facts


def audit(paths):
    reasons, points = Counter(), []
    for path in paths:
        state = None
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if row['kind'] == 'state': state = row['state']
            if row['kind'] != 'model' or not state or state.get('state_type') not in COMBAT: continue
            result = search_turn(state, include_candidates=True)
            offer = choice_offer(state, result)
            player = state['player']
            if offer: reason = 'candidate_eligible'
            elif result['reason'] not in {'supported_turn', 'unsupported_hand', 'no_supported_moves', 'needs_potion_evaluation', 'random_orb_targets', 'mixed_orb_order'}:
                reason = result['reason']
            elif player['hp'] < .3 * player['max_hp']: reason = 'low_hp'
            elif combat_facts(state)['lethal_if_no_more_block']: reason = 'lethal_intent'
            elif combat_facts(state)['block_needed_for_no_damage'] > 0 and any(p.get('can_use_in_combat') for p in player.get('potions') or []):
                reason = 'potion_choice'
            else: reason = 'no_menu_or_missing_state'
            reasons[reason] += 1
            points.append({'file': str(path), 'floor': state.get('run', {}).get('floor'),
                           'round': state.get('battle', {}).get('round'), 'reason': reason,
                           'relics': [r['id'] for r in player.get('relics') or []]})
    return {'reasons': dict(reasons), 'points': points}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='+', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.logs), ensure_ascii=False, indent=2))
