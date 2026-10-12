"""Find exam cases in the takeover logs whose right answer the program can tell on its own.

Each rule below names a check the program computes (a found kill, a potion not worth it in an
ordinary fight, spare energy, a recommended rest, Strikes that still carry the damage, an event
option the community does much worse with). Matching states become regression cases with a tag,
at most --per-tag per rule, spread over different logs. Nothing calls a model.

    python tools/exam/mine_exam.py --list            # what would be added
    python tools/exam/mine_exam.py --per-tag 10      # write them to tests/regression/
"""
import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_eval  # noqa: E402
import regression  # noqa: E402
from core.spire_codex import event_advice  # noqa: E402
from deck.direction import deck_direction  # noqa: E402
from combat.facts import combat_facts, gives_energy_now  # noqa: E402
from route.facts import rest_facts  # noqa: E402
from combat.search import card_move, search_turn  # noqa: E402

EVENT_LIFT_GAP = 4  # Community lift points between an event's best and worst option.


def kill(case):
    """The search found an exact kill made of plain damage cards (orb damage is not scored)."""
    result = search_turn(case['state'])
    if result['reason'] != 'direct_win': return None
    hand = {c.get('index'): c for c in case['state']['player'].get('hand') or []}
    for step in result.get('sequence') or []:
        move = card_move(hand.get(step.get('card_index'), {}))
        if move is None or move.channel or move.evokes: return None
    return {'kills_all': ['=', True]}, '這回合打得死所有敵人，應該直接打死'


def potion_safe(case):
    facts = combat_facts(case['state'])
    if case['state'].get('state_type') != 'monster' or 'normal_fight_potion_rule' not in facts: return None
    if any(p.get('worth_it_in_this_normal_fight') for p in facts['usable_potions']): return None
    return {'potion_drunk_when_safe': ['=', False]}, '普通戰鬥不致命，藥水也省不了兩成血，應留著'


def potion_lethal(case):
    facts = combat_facts(case['state'])
    if not facts.get('potion_block_covers_lethal'): return None
    return {'potion_held_when_lethal': ['=', False]}, '不擋會死，格擋藥水能救命，應該喝'


def energy(case):
    state = case['state']
    if combat_facts(state)['spare_energy_without_energy_cards'] < 0: return None
    if not any(c.get('can_play') and gives_energy_now(c) for c in state['player'].get('hand') or []): return None
    return {'wasted_energy_card': ['=', False]}, '能量已夠打完手牌，不要打加能量的牌'


def rest(case, path):
    route = regression.as_route(regression.played_route(path, case))
    if not route or not rest_facts(case['state'], route).get('rest_recommended'): return None
    return {'rested': ['=', True]}, '照預估到下個休息點前會跌破三成血，應該回血', route


def removal(case):
    screen = case['state'].get('card_select') or {}
    if '移除' not in str(screen.get('prompt') or ''): return None
    if not deck_direction(case['state'])[0].get('protect_starter_damage'): return None
    names = [c.get('name') for c in screen.get('cards') or []]
    if not any(n in ('打击', '打擊') for n in names) or all(n in ('打击', '打擊') for n in names): return None
    return {'selected_strike': ['=', False]}, '第 1 幕輸出還靠打擊，不要刪打擊'


def event(case):
    state = case['state']; screen = state.get('event') or {}
    advice = event_advice(screen.get('event_id'), screen.get('options') or [],
                          (state.get('player') or {}).get('character_id') or '') or []
    rated = [(a.get('community') or a.get('relic_rating') or {}).get('lift') for a in advice]
    rated = [(lift, a['title']) for lift, a in zip(rated, advice) if lift is not None]
    if len(rated) < 2: return None
    (low, worst), (high, best) = min(rated), max(rated)
    if high - low < EVENT_LIFT_GAP: return None
    return {'picked_title': ['!=', worst]}, f'社群 lift：{best} {high:+}、{worst} {low:+}，不要選 {worst}'


def tempo(case):
    """Healthy HP and a turn that is not lethal: block past the incoming damage is waste, and some damage must be dealt."""
    facts = combat_facts(case['state'])
    if 'can_afford_to_attack' not in facts: return None
    if not any(c.get('can_play') and card_move(c) is not None for c in case['state']['player'].get('hand') or []): return None
    return {'block_surplus': ['<=', 0], 'damage': ['>=', 1]}, '血量夠厚且不致命，格擋不要溢出，要打出傷害'


COMBAT_RULES = {'kill': kill, 'potion': potion_safe, 'potion_lethal': potion_lethal, 'energy': energy, 'tempo': tempo}


def mine(per_tag, extra_logs=()):
    found = collections.defaultdict(list)
    seen = set()
    for path in sorted(replay_eval.LOGS.glob('*.jsonl')) + sorted(Path(p) for p in extra_logs):
        try: cases = replay_eval.build_cases([path])
        except (ValueError, KeyError): continue
        for case in cases:
            key = replay_eval.fingerprint(case['state'])
            if key in seen: continue
            seen.add(key)
            hits = []
            try:
                if case['type'] == 'combat':
                    hits = [(tag, rule(case)) for tag, rule in COMBAT_RULES.items()]
                elif case['type'] == 'rest_site': hits = [('rest', rest(case, path))]
                elif case['type'] == 'card_select': hits = [('removal', removal(case))]
                elif case['type'] == 'event': hits = [('event', event(case))]
            except (KeyError, TypeError, ValueError):
                continue
            for tag, hit in hits:
                if hit: found[tag].append((path, case, hit))
    chosen = {}
    for tag, rows in found.items():
        by_log = collections.defaultdict(list)
        for row in rows: by_log[row[0].stem].append(row)
        picked = []
        while len(picked) < per_tag and any(by_log.values()):  # Round-robin over logs for variety.
            for log in list(by_log):
                if by_log[log] and len(picked) < per_tag: picked.append(by_log[log].pop(0))
        chosen[tag] = picked
    return chosen, {tag: len(rows) for tag, rows in found.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--per-tag', type=int, default=10)
    parser.add_argument('--logs', nargs='*', default=[], help='extra takeover logs (e.g. lab batch *-takeover.jsonl)')
    parser.add_argument('--list', action='store_true', help='only print what would be written')
    args = parser.parse_args()
    chosen, totals = mine(args.per_tag, args.logs)
    existing = {c['id'] for c in regression.load()}
    for tag, rows in sorted(chosen.items()):
        print(f'{tag}: {len(rows)} 題（符合 {totals[tag]}）')
        for path, case, hit in rows:
            expect, note, *route = hit
            if case['id'] in existing: continue
            original = replay_eval.score(case, case['original'])
            passed = not regression.failures({**case, 'expect': expect}, case['original'])
            print(f"  {case['id']}  {note}  原本{'通過' if passed else '答錯'}")
            if args.list: continue
            target = regression.SUITE / ('mined-' + case['id'].replace('#', '_') + '.json')
            target.write_text(json.dumps({'id': case['id'], 'type': case['type'], 'tags': [tag], 'note': note,
                                          'expect': expect, 'original': case['original'], 'state': case['state'],
                                          **({'route': route[0] and {**route[0], 'path': [list(n) for n in route[0]['path']],
                                              'types': [[c, r, t] for (c, r), t in route[0]['types'].items()]}} if route else {})},
                                         ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
