"""Whole-fight benchmark: community fights rebuilt in the battle lab, played by a model, compared with the player.

Spire Codex runs record every fight's encounter, the deck and relics of that floor, HP before and the
damage taken and turns the player needed. The battle lab rebuilds the fight; takeover plays it.

    python tools/exam/fight_bench.py sample --count 30 --acts 1 --name act1
    python tools/exam/fight_bench.py run --name act1 --config claude:haiku@low
    python tools/exam/fight_bench.py report --name act1

Only fights the lab can rebuild are used: every relic with a combat effect is one the lab supports
(relics that act only when picked up are left out, their effect is already in the deck and HP), no
enchanted card, and the player drank no potion (the lab starts the fight without potions). Draws and
enemy rolls are new, so one fight says little; compare averages over many.
"""
import argparse
import json
import random
import re
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_runs as runs  # noqa: E402

OUT = ROOT / 'vakuu' / 'button-runtime' / 'fight-bench'
API = 'http://127.0.0.1:15526'
COMBAT = {'monster', 'elite', 'boss'}
# A relic sentence that does nothing in a fight: it acts on pickup, or mentions nothing fights involve.
FIGHT_WORDS = re.compile(r'战斗|回合|打出|格挡|伤害|抽|能量|敌人|生命')


def lab_relics():
    """Relic ids the battle lab accepts, read from the mod source."""
    source = (ROOT / 'mod' / 'McpMod.BattleLab.cs').read_text(encoding='utf-8')
    block = source.split('LabRelics = new(StringComparer.Ordinal)')[1].split('};')[0]
    return set(re.findall(r'"([A-Z_]+)"', block))


def pickup_only(relic_id, relics):
    text = runs.spire_codex._plain((relics.get(relic_id) or {}).get('description'))
    parts = [s for s in re.split(r'[。\n]', text) if s.strip()]
    return bool(parts) and all(re.match(r'\s*(拾起时|获得时|拾取时)', s) or not FIGHT_WORDS.search(s) for s in parts)


def fights(path, run, supported, relics):
    """Rebuildable fights of one run, each with its lab spec and the player's result."""
    player = run['players'][0]
    character = runs.bare(player.get('character'))
    previous = None
    for floor, act, point in runs.points(run):
        stats = (point.get('player_stats') or [{}])[0]
        rooms = point.get('rooms') or []
        encounter = str((rooms[0] if rooms else {}).get('model_id') or '')
        before = previous
        previous = stats
        if not encounter.startswith('ENCOUNTER.') or before is None or stats.get('potion_used'): continue
        deck = runs.deck_before(run, player, floor)
        if any(card.get('enchantment') for card in deck): continue
        held = {runs.bare(r['id']) for r in player.get('relics') or [] if (r.get('floor_added_to_deck') or 0) < floor}
        if held - supported - {r for r in held if pickup_only(r, relics)}: continue
        kind = point.get('map_point_type')
        act_start = next(f for f, a, _ in runs.points(run) if a == act)
        yield {
            'id': f'{path.stem}#{floor}', 'kind': 'monster' if kind not in COMBAT else kind, 'act': act,
            'encounter': encounter.split('.', 1)[1], 'won_run': bool(run.get('win')),
            'player': {'damage': stats.get('damage_taken') or 0, 'turns': rooms[0].get('turns_taken'),
                       'hp_before': before.get('current_hp')},
            'spec': {'character': character, 'encounter': encounter.split('.', 1)[1],
                     'ascension': min(10, run.get('ascension') or 0), 'act': act, 'floor': floor,
                     'act_floor': floor - act_start + 1, 'hp': before.get('current_hp'), 'max_hp': before.get('max_hp'),
                     'gold': before.get('current_gold') or 0,
                     'deck': [{'id': runs.bare(c['id']), 'upgrades': c.get('current_upgrade_level') or 0} for c in deck],
                     'relics': sorted(held & supported), 'potions': []},
        }


def sample(count, acts, seed, name):
    supported, relics = lab_relics(), runs.catalog('relics')
    pool = defaultdict(list)
    for path in sorted(runs.RUNS.glob('*-*.json')):
        run = json.loads(path.read_text(encoding='utf-8'))
        if len(run.get('players') or []) != 1: continue
        for fight in fights(path, run, supported, relics):
            if fight['act'] in acts and fight['spec']['hp']: pool[fight['kind']].append(fight)
    rng = random.Random(seed)
    for group in pool.values(): rng.shuffle(group)
    # Elites and bosses decide runs; give them a third each of the sample where the pool allows.
    share = {'elite': count // 3, 'boss': count // 6}
    chosen = [f for kind, n in share.items() for f in pool[kind][:n]]
    chosen += pool['monster'][:count - len(chosen)]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'{name}.json').write_text(json.dumps(chosen, ensure_ascii=False, indent=1), encoding='utf-8')
    print({kind: len(group) for kind, group in pool.items()}, '→', Counter(f['kind'] for f in chosen), OUT / f'{name}.json')


def game_state():
    return json.load(urllib.request.urlopen(API + '/api/v1/singleplayer?format=json', timeout=10))


def to_menu():
    if game_state().get('state_type') == 'game_over':
        request = urllib.request.Request(API + '/api/v1/singleplayer', data=json.dumps(
            {'action': 'menu_select', 'option': 'main_menu'}).encode(), headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(request, timeout=20); time.sleep(5)


def outcome(log, hp_before):
    """Damage, turns and death from a takeover log of one fight."""
    last = None
    for line in open(log, encoding='utf-8'):
        record = json.loads(line)
        if record['kind'] == 'state' and record['state'].get('state_type') in COMBAT: last = record['state']
    end = game_state()
    died = end.get('state_type') == 'game_over'
    hp = 0 if died else ((last or {}).get('player') or {}).get('hp', hp_before)
    return {'damage': hp_before - hp, 'turns': ((last or {}).get('battle') or {}).get('round'), 'died': died,
            'finished': died or end.get('state_type') not in COMBAT | {'hand_select', 'card_select'}}


def run(name, config, playbook):
    import battle_lab
    chosen = json.loads((OUT / f'{name}.json').read_text(encoding='utf-8'))
    tag = config.replace(':', '-').replace('@', '-') + ('-playbook' if playbook else '')
    results = OUT / f'{name}.{tag}.jsonl'
    done = {json.loads(l)['id'] for l in open(results, encoding='utf-8')} if results.exists() else set()
    model, effort = (config.split('@') + ['low'])[:2]
    for n, fight in enumerate(chosen, 1):
        if fight['id'] in done: continue
        to_menu()
        battle_lab.request(API, {'action': 'start', 'spec': fight['spec']})
        time.sleep(5)
        battle_lab.request(API, {'action': 'stop'})  # Recording blocks takeover's actions.
        log = OUT / 'logs' / f"{name}.{tag}.{fight['id'].replace('#', '_')}.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, '-X', 'utf8', str(ROOT / 'tools' / 'takeover_log.py'), '--one-fight', '--steps', '400',
                   '--model', model, '--effort', effort, '--log', str(log)] + (['--playbook', playbook] if playbook else [])
        subprocess.run(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        result = {'id': fight['id'], 'kind': fight['kind'], **outcome(log, fight['spec']['hp'])}
        with open(results, 'a', encoding='utf-8') as out: out.write(json.dumps(result, ensure_ascii=False) + '\n')
        print(f"{n}/{len(chosen)} {fight['id']} {fight['kind']} {fight['encounter']}：模型掉 {result['damage']}"
              f"（玩家 {fight['player']['damage']}），{result['turns']} 回合（玩家 {fight['player']['turns']}）"
              f"{'，死亡' if result['died'] else ''}{'' if result['finished'] else '，未打完'}", flush=True)


def report(name):
    chosen = {f['id']: f for f in json.loads((OUT / f'{name}.json').read_text(encoding='utf-8'))}
    for results in sorted(OUT.glob(f'{name}.*.jsonl')):
        rows = [json.loads(l) for l in open(results, encoding='utf-8')]
        rows = [r for r in rows if r['finished'] and r['id'] in chosen]
        print(f"\n{results.stem.split('.', 1)[1]}：{len(rows)} 場")
        for kind in ('monster', 'elite', 'boss', None):
            group = [r for r in rows if kind is None or r['kind'] == kind]
            if not group: continue
            extra = [r['damage'] - chosen[r['id']]['player']['damage'] for r in group]
            print(f"  {kind or '全部':8} {len(group):3} 場  比玩家多掉血 平均 {sum(extra) / len(group):+.1f}"
                  f"  死亡 {sum(r['died'] for r in group)}  掉血不超過玩家 {sum(e <= 0 for e in extra)} 場")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('sample')
    p.add_argument('--count', type=int, default=30)
    p.add_argument('--acts', type=int, nargs='+', default=[1])
    p.add_argument('--seed', type=int, default=1)
    p.add_argument('--name', default='act1')
    p = sub.add_parser('run')
    p.add_argument('--name', default='act1')
    p.add_argument('--config', default='claude:haiku@low')
    p.add_argument('--playbook', help='model that writes the deck playbook, e.g. codex:gpt-6.1-sol@low')
    p = sub.add_parser('report')
    p.add_argument('--name', default='act1')
    args = parser.parse_args()
    if args.command == 'sample': sample(args.count, set(args.acts), args.seed, args.name)
    elif args.command == 'run': run(args.name, args.config, args.playbook)
    else: report(args.name)


if __name__ == '__main__': main()
