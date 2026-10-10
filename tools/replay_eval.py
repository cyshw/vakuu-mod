"""Replay recorded game states to several model configurations and score their decisions offline.

Live takeover runs are slow, noisy (every run differs) and can lose the game; replaying the exact same
recorded decision points to each configuration compares them directly. Cases come from the JSONL logs
written by tools/takeover_log.py (the state each model decision was made on).

    python tools/replay_eval.py --per-type 6 --configs claude:haiku claude:sonnet codex:gpt-6.1-sol
    python tools/replay_eval.py --report vakuu/button-runtime/eval/<run>/results.jsonl
"""
import argparse
import collections
import json
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / 'vakuu'
sys.path.insert(0, str(SOURCE))
import vakuu_engine as engine  # noqa: E402
from sts2_advisor import advise  # noqa: E402
from vakuu_facts import card_cost, combat_facts, gives_energy_now, route_facts  # noqa: E402
from vakuu_rules import COMBAT, END_TURN, fingerprint, transitional, validate  # noqa: E402

LOGS = SOURCE / 'button-runtime' / 'logs'
OUT = SOURCE / 'button-runtime' / 'eval'
TYPES = {'combat': COMBAT, 'map': {'map'}, 'rest_site': {'rest_site'}, 'card_reward': {'card_reward'},
         'shop': {'shop'}, 'event': {'event'}, 'card_select': {'card_select'}}
BLOCK = re.compile(r'获得(\d+)点格挡')
DAMAGE = re.compile(r'造成(\d+)点伤害(?:(\d+)次)?')
ENERGY = re.compile(r'获得(\d+)\[')


def case_type(state):
    kind = state.get('state_type')
    return next((name for name, kinds in TYPES.items() if kind in kinds), None)


def build_cases(paths=None):
    """Every state a model decided on, deduplicated, skipping screens caught mid-animation."""
    cases, seen = [], set()
    for path in paths or sorted(LOGS.glob('*.jsonl')):
        last = None
        for line in open(path, encoding='utf-8'):
            record = json.loads(line)
            if record['kind'] == 'state': last = record['state']
            elif record['kind'] == 'model' and last:
                kind = case_type(last)
                key = fingerprint(last)
                if kind and key not in seen and not transitional(last):
                    if kind == 'combat' and not (last.get('player') or {}).get('hand'): continue
                    if kind == 'map' and len((last.get('map') or {}).get('next_options') or []) < 2: continue
                    seen.add(key)
                    cases.append({'id': f'{path.stem}#{len(cases)}', 'type': kind, 'state': last,
                                  'original': record['decision'], 'original_model': record.get('model')})
    return cases


def pick(cases, per_type, seed):
    rng = random.Random(seed)
    by_type = collections.defaultdict(list)
    for case in cases: by_type[case['type']].append(case)
    chosen = []
    for kind, group in sorted(by_type.items()):
        chosen += rng.sample(group, min(per_type, len(group)))
    return chosen


def decide(config, state):
    controller = engine.Controller(lambda message: None)
    model, effort = (config.split('@') + ['low'])[:2]
    selection = {'model_key': model, 'effort': effort}
    if model.startswith('auto:'):
        saved = engine.models.settings
        engine.models.settings = lambda: selection  # Routing reads settings; not thread-safe across auto configs.
        try: return controller.decide_routed(state)
        finally: engine.models.settings = saved
    return controller.decide(state, selection)


def plan_cards(state, decision):
    """Hand cards the decision plays this turn (first action plus next_cards) and whether it ends the turn."""
    hand = (state.get('player') or {}).get('hand') or []
    indices = []
    if decision['action'] == 'play_card':
        indices.append(json.loads(decision['parameters_json']).get('card_index'))
    ends = decision['action'] == 'end_turn'
    for entry in decision.get('next_cards') or []:
        if isinstance(entry.get('potion_slot'), int) and entry['potion_slot'] >= 0: continue  # A potion, not a card.
        if entry.get('hand_index') == END_TURN: ends = True; break
        indices.append(entry.get('hand_index'))
    cards = [hand[i] for i in indices if isinstance(i, int) and 0 <= i < len(hand)]
    return cards, ends, len(cards) != len(indices) or len(set(indices)) != len(indices)


def score_combat(state, decision):
    player = state.get('player') or {}
    facts = combat_facts(state)
    cards, ends, broken = plan_cards(state, decision)
    gained = sum(int(m.group(1)) for c in cards if gives_energy_now(c) for m in [ENERGY.search(c.get('description') or '')] if m)
    budget = (player.get('energy') or 0) + gained
    cost = sum(card_cost(c) for c in cards)
    block = sum(int(m.group(1)) for c in cards for m in BLOCK.finditer(c.get('description') or ''))
    damage = sum(int(m.group(1)) * int(m.group(2) or 1) for c in cards for m in DAMAGE.finditer(c.get('description') or ''))
    slots = set(drunk_potions(decision))
    potions = [p for p in facts.get('usable_potions') or [] if p['slot'] in slots]
    block += sum(p.get('block', 0) for p in potions)
    unblocked = max(0, facts['incoming_attack_damage'] - facts['current_block'] - block)
    played = {id(c) for c in cards}
    affordable_left = [c for c in player.get('hand') or [] if id(c) not in played and c.get('can_play')
                       and not gives_energy_now(c) and card_cost(c) <= budget - cost]
    return {'plan_cards': len(cards), 'complete_turn': ends, 'broken_plan': broken,
            'overspend': cost > budget, 'unused_energy': max(0, budget - cost) if ends else None,
            'left_affordable_cards': len(affordable_left) if ends else None,
            'block': block, 'damage': damage, 'unblocked': unblocked if ends else None,
            'dies': (unblocked >= (player.get('hp') or 0)) if ends else None,
            'wasted_energy_card': any(gives_energy_now(c) for c in cards) and facts['spare_energy_without_energy_cards'] >= 0,
            # Potions both ways: held while the turn is lethal, and drunk in an ordinary fight that was safe.
            'potion_held_when_lethal': bool(facts.get('usable_potions')) and facts['lethal_if_no_more_block'] and not potions,
            'potion_drunk_when_safe': bool(potions) and state.get('state_type') == 'monster'
                                      and not facts['lethal_if_no_more_block'] and not facts.get('potion_kills_an_enemy')}


def drunk_potions(decision):
    """Potion slots the decision drinks this turn, as the first action or inside next_cards."""
    slots = []
    if decision['action'] == 'use_potion':
        slots.append(json.loads(decision['parameters_json']).get('slot'))
    slots += [e['potion_slot'] for e in decision.get('next_cards') or []
              if isinstance(e.get('potion_slot'), int) and e['potion_slot'] >= 0]
    return slots


def score(case, decision):
    state, kind = case['state'], case['type']
    try:
        validate(state, decision['action'], json.loads(decision['parameters_json']))
        result = {'valid': decision['action'] != 'none'}
    except (ValueError, TypeError) as exc:
        result = {'valid': False, 'invalid_reason': str(exc)}
    if kind == 'combat' and result['valid']: result.update(score_combat(state, decision))
    if kind == 'rest_site' and decision['action'] == 'choose_rest_option':
        player = state['player']
        options = state['rest_site'].get('options') or []
        index = json.loads(decision['parameters_json']).get('index')
        name = next((o.get('name') for o in options if o.get('index') == index), None)
        result.update(rested=name == '休息', guide_says_rest=player['hp'] / player['max_hp'] < 0.4)
    if kind == 'card_reward':
        rows = advise(state).get('ranked_cards') or []
        best = max((r['score'] for r in rows if r.get('score') is not None), default=None)
        picked = None
        if decision['action'] == 'select_card_reward':
            index = json.loads(decision['parameters_json']).get('card_index')
            picked = next((r['score'] for r in rows if r['index'] == index), None)
        result.update(skipped=decision['action'] == 'skip_card_reward', picked_score=picked, best_score=best)
    if kind == 'map' and decision['action'] == 'choose_map_node':
        index = json.loads(decision['parameters_json']).get('index')
        option = next((o for o in route_facts(state)['options'] if o['index'] == index), None)
        if option: result.update(rooms_before_elite=option['max_rooms_before_first_elite'],
                                 hp_ratio=route_facts(state)['hp_ratio'])
    return result


def run(cases, configs, workers, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    jobs = [(case, config) for case in cases for config in configs]
    print(f'{len(cases)} cases x {len(configs)} configs = {len(jobs)} model calls', flush=True)
    with open(out, 'a', encoding='utf-8') as file, ThreadPoolExecutor(workers) as pool:
        # 'original' replays what was decided live (no model call), to compare before and after a change.
        call = lambda c, k: (time.time(), c['original'] if k == 'original' else decide(k, c['state']))
        futures = {pool.submit(call, case, config): (case, config) for case, config in jobs}
        for done, future in enumerate(as_completed(futures), 1):
            case, config = futures[future]
            try:
                started, decision = future.result()
                row = {'case': case['id'], 'type': case['type'], 'config': config, 'seconds': round(time.time() - started, 1),
                       'decision': decision, 'score': score(case, decision)}
            except Exception as exc:  # One failed call must not lose the rest of the batch.
                row = {'case': case['id'], 'type': case['type'], 'config': config, 'error': str(exc)[:300]}
            file.write(json.dumps(row, ensure_ascii=False) + '\n'); file.flush()
            print(f"[{done}/{len(jobs)}] {config} {case['type']} {row.get('seconds', 'ERR')}", flush=True)


def mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else None


def report(results_path):
    rows = [json.loads(line) for line in open(results_path, encoding='utf-8')]
    configs = sorted({r['config'] for r in rows})
    lines = [f'# 離線重播評估：{results_path.parent.name}', '',
             '| 設定 | 呼叫 | 錯誤 | 合法 | 平均秒數 | 戰鬥：會死 | 戰鬥：平均漏傷 | 戰鬥：能量超支 | 戰鬥：剩能量又有牌可打 | 浪費加能量牌 | 致命卻沒喝藥水 | 安全時喝藥水 | 休息點照規則 | 選牌分數（最佳=1） | 低血時選了很快遇菁英的路 |',
             '|' + '---|' * 15]
    for config in configs:
        mine = [r for r in rows if r['config'] == config]
        ok = [r for r in mine if 'score' in r]
        fights = [r['score'] for r in ok if r['type'] == 'combat' and r['score'].get('complete_turn')]
        rests = [r['score'] for r in ok if r['type'] == 'rest_site' and 'rested' in r['score']]
        picks = [r['score'] for r in ok if r['type'] == 'card_reward' and r['score'].get('best_score')]
        routes = [r['score'] for r in ok if r['type'] == 'map' and 'rooms_before_elite' in r['score']]
        lines.append('| ' + ' | '.join(str(x) for x in [
            config, len(mine), len(mine) - len(ok), f"{sum(r['score']['valid'] for r in ok)}/{len(ok)}",
            mean([r['seconds'] for r in ok]),
            f"{sum(bool(f['dies']) for f in fights)}/{len(fights)}", mean([f['unblocked'] for f in fights]),
            sum(f['overspend'] for f in fights), sum(bool(f['left_affordable_cards']) for f in fights),
            sum(f['wasted_energy_card'] for f in [r['score'] for r in ok if r['type'] == 'combat' and 'wasted_energy_card' in r['score']]),
            sum(bool(f.get('potion_held_when_lethal')) for f in [r['score'] for r in ok if r['type'] == 'combat']),
            sum(bool(f.get('potion_drunk_when_safe')) for f in [r['score'] for r in ok if r['type'] == 'combat']),
            f"{sum(r['rested'] == r['guide_says_rest'] for r in rests)}/{len(rests)}",
            mean([(p['picked_score'] or 0) / p['best_score'] for p in picks if not p['skipped']]),
            f"{sum(r['rooms_before_elite'] <= 2 and r['hp_ratio'] < 0.5 for r in routes)}/{len(routes)}",
        ]) + ' |')
    lines += ['', '「會死」與「漏傷」只算排完整個回合的計畫，依卡牌說明估算格擋（含計畫中喝的藥水），未計充能球與能力。「安全時喝藥水」指普通戰鬥、不致命、藥水也打不死敵人時就喝。', '', '## 各設定判斷不同的局面', '']
    by_case = collections.defaultdict(dict)
    for r in rows:
        if 'decision' in r: by_case[(r['case'], r['type'])][r['config']] = r
    for (case, kind), answers in sorted(by_case.items()):
        moves = {c: (a['decision']['action'], a['decision']['parameters_json']) for c, a in answers.items()}
        if len(set(moves.values())) <= 1: continue
        lines.append(f'### {case}（{kind}）')
        for config, answer in answers.items():
            advice = answer['decision']['advice'].replace('\n', ' ')
            lines.append(f"- **{config}**：`{moves[config][0]} {moves[config][1]}` {json.dumps(answer['score'], ensure_ascii=False)}  \n  {advice}")
        lines.append('')
    target = results_path.with_name('report.md')
    target.write_text('\n'.join(lines), encoding='utf-8')
    print('report:', target)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--configs', nargs='+', default=['claude:haiku', 'claude:sonnet'],
                        help='model keys, optionally with @effort, e.g. claude:sonnet@high')
    parser.add_argument('--per-type', type=int, default=6, help='cases sampled per decision type')
    parser.add_argument('--seed', type=int, default=1, help='same seed = same cases, so runs stay comparable')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--report', type=Path, help='only write the report for an existing results.jsonl')
    parser.add_argument('--logs', type=Path, nargs='+', help='take cases only from these log files')
    parser.add_argument('--all', action='store_true', help='use every case instead of sampling per type')
    parser.add_argument('--types', nargs='+', help='only these case types, e.g. map combat')
    args = parser.parse_args()
    if args.report: return report(args.report)
    pool = [c for c in build_cases(args.logs) if not args.types or c['type'] in args.types]
    cases = pool if args.all else pick(pool, args.per_type, args.seed)
    out = OUT / time.strftime('%Y%m%d-%H%M%S') / 'results.jsonl'
    run(cases, args.configs, args.workers, out)
    report(out)


if __name__ == '__main__':
    main()
