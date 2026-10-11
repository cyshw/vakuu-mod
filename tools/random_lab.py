"""Choose a source-verified practice encounter uniformly for bug testing.

Selection only: no game commands and no model calls.
"""
import argparse
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HASH = 'af89e3c82bb6f873'
FLOORS = (2, 5, 6, 8, 11, 12, 15)


def encounter_groups(pool):
    groups = {}
    for candidate in pool:
        name = candidate['spec']['encounter']
        for suffix in ('_WEAK', '_NORMAL'):
            if name.endswith(suffix): name = name[:-len(suffix)]; break
        groups.setdefault(name, []).append(candidate)
    return groups


def sample_pool(pool, rng=None, grindy=False):
    rng = rng or random.SystemRandom()
    groups = encounter_groups(pool)
    names = list(groups)
    weights = [max(c['weight'] for c in groups[name]) if grindy else 1 for name in names]
    name = rng.choices(names, weights=weights, k=1)[0]
    return rng.choice(groups[name])


def difficulty_weight(hp, max_hp, damage, turns):
    """Observable proxies for a grind; not a prediction of optimal play."""
    health = hp / max_hp
    return (1 + (2 if health <= .5 else 1 if health <= .75 else 0)
            + (2 if damage >= 15 else 1 if damage >= 8 else 0)
            + (1 if turns >= 5 else 0))


def candidates(run):
    # This source's early history was audited: no transforms/enchants or
    # unsupported relics before these fights. Do not apply to arbitrary runs.
    assert run['build_id'] == 'v0.111.0' and run['ascension'] == 10
    assert run['players'][0]['character'] == 'CHARACTER.IRONCLAD'
    rows = [row for act in run['map_point_history'] for row in act]
    result = []
    for floor in FLOORS:
        deck = ([{'id': 'STRIKE_IRONCLAD', 'upgrades': 0} for _ in range(5)]
                + [{'id': 'DEFEND_IRONCLAD', 'upgrades': 0} for _ in range(4)]
                + [{'id': name, 'upgrades': 0} for name in ('BASH', 'ASCENDERS_BANE')])
        potions = []
        for row in rows[:floor - 1]:
            stats = row['player_stats'][0]
            for card in stats.get('cards_gained', []):
                deck.append({'id': card['id'].split('.')[-1],
                             'upgrades': card.get('current_upgrade_level', 0)})
            for card in stats.get('cards_removed', []):
                deck.remove(next(c for c in deck if c['id'] == card['id'].split('.')[-1]
                                 and c['upgrades'] == card.get('current_upgrade_level', 0)))
            for name in stats.get('upgraded_cards', []):
                next(c for c in deck if c['id'] == name.split('.')[-1] and not c['upgrades'])['upgrades'] += 1
            for name in stats.get('potion_used', []): potions.remove(name.split('.')[-1])
            for potion in stats.get('potion_choices', []):
                if potion.get('was_picked'): potions.append(potion['choice'].split('.')[-1])
        before, fight = rows[floor - 2]['player_stats'][0], rows[floor - 1]
        room = fight['rooms'][0]
        damage, turns = fight['player_stats'][0]['damage_taken'], room['turns_taken']
        spec = {'character': 'IRONCLAD', 'encounter': room['model_id'].split('.')[-1],
                'ascension': 10, 'act': 1, 'floor': floor, 'act_floor': floor,
                'hp': before['current_hp'], 'max_hp': before['max_hp'], 'gold': before['current_gold'],
                'deck': deck, 'potions': potions,
                'relics': [r['id'].split('.')[-1] for r in run['players'][0]['relics']
                           if r['floor_added_to_deck'] < floor]}
        result.append({'spec': spec, 'source_damage': damage, 'source_turns': turns,
                       'weight': difficulty_weight(spec['hp'], spec['max_hp'], damage, turns)})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true', help='Show probabilities without choosing')
    parser.add_argument('--grindy', action='store_true', help='Explicitly opt in to difficulty-weighted sampling')
    parser.add_argument('--output', type=Path, default=ROOT / 'vakuu/button-runtime/lab/random-config.json')
    args = parser.parse_args()
    run = json.loads((ROOT / f'build/spire-lab/{SOURCE_HASH}.json').read_text(encoding='utf-8'))
    pool = candidates(run)
    groups = encounter_groups(pool)
    weights = {name: max(c['weight'] for c in group) if args.grindy else 1
               for name, group in groups.items()}
    total = sum(weights.values())
    probabilities = [{'floor': c['spec']['floor'], 'encounter': c['spec']['encounter'],
                      'family': name, 'family_probability': weights[name] / total,
                      'probability': weights[name] / total / len(group)}
                     for name, group in groups.items() for c in group]
    if args.preview:
        print(json.dumps(probabilities, indent=2)); return
    chosen = sample_pool(pool, grindy=args.grindy)
    chosen = {**chosen, 'difficulty_weight': chosen['weight'], 'weight': chosen['weight'] if args.grindy else 1,
              'sampling': 'grindy_encounter' if args.grindy else 'uniform_encounter'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({**chosen, 'source': f'https://spire-codex.com/runs/{SOURCE_HASH}',
                                      'pool': probabilities}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Selected floor {chosen['spec']['floor']}; config: {args.output}")


if __name__ == '__main__': main()
