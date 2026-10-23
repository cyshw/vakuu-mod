"""Card picks measured against community runs on Spire Codex.

Spire Codex keeps every submitted run with its floor-by-floor history (cards offered and picked,
removed, transformed, upgraded). The deck at any card reward is rebuilt from that history, so a
question shows the deck the player really had on that floor, not the winning deck it became.

    python tools/exam/codex_runs.py fetch --characters IRONCLAD DEFECT --runs 150
    python tools/exam/codex_runs.py agree --config claude:haiku@low --spots 300
    python tools/exam/codex_runs.py agree --config claude:haiku@low --spots 300 --mode free

Act 1 card rewards have no clearly wrong answer in the community's overall card statistics (the
common cards' win-rate lifts all sit within about one point), so instead of pass/fail cases this
reports how often the model picks what the A10 player picked, overall and in won runs. One
player's pick is noisy; the agreement rate over many spots compares two versions of the prompt.

Measured 2026-10-13 on act 1 rewards: always taking the best-rated card matched the player 41% of
300 spots (42% of 300 in won runs, fetched with --wins); Spire Codex's deck-aware pick coach matched
40% / 42%, no better, so it was not adopted. Haiku matched 38% of 60. A 60-spot sample once showed
the pick coach 13 points ahead: use 300 spots before trusting a difference of a few points.

--mode top (the default, as in takeover) takes the best-rated card without asking the model; rating
shows the model only that card to take or skip (29% on seed 7: Haiku skipped 152 of 300); free lets
it choose among all offers. Skips are scored apart: how many skipped picks the player also skipped,
and how many of the player's skips were caught.

    python tools/exam/codex_runs.py sweep --spots 300 --seed 7

sweep asks no model: it tries "take the best-rated card unless its score is below N (in a deck of at
least D cards)" for a grid of N and D, to see whether any plain skip rule beats always taking it.
"""
import argparse
import json
import random
import sys
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'vakuu'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import spire_codex  # noqa: E402

RUNS = ROOT / 'vakuu' / 'button-runtime' / 'codex-runs'
API = 'https://spire-codex.com/api'
HEADERS = {'User-Agent': 'vakuu-mod/0.2 (STS2 assistant exam builder)'}
PAUSE = 0.5  # Seconds between requests; the site is a community service.
CATALOG_AGE = 7 * 24 * 3600


def get(path):
    request = urllib.request.Request(API + path, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def fetch(characters, runs, ascension, wins=False):
    """Download single-player A10+ runs that are not abandoned; wins and losses alike unless wins."""
    RUNS.mkdir(parents=True, exist_ok=True)
    for character in characters:
        prefix = f'{character}-win' if wins else character
        have = [path for path in RUNS.glob(f'{prefix}-*.json') if wins or '-win-' not in path.name]
        page = 1
        while len(have) < runs:
            listing = get(f'/runs/list?character={character}&ascension_min={ascension}&players=1&limit=100&page={page}'
                          + ('&win=true' if wins else ''))
            if not listing.get('runs'): break
            for row in listing['runs']:
                if len(have) >= runs: break
                path = RUNS / f"{prefix}-{row['run_hash']}.json"
                if row.get('was_abandoned') or path.exists() or (RUNS / f"{character}-{row['run_hash']}.json").exists() or row.get('game_mode') != 'standard': continue
                time.sleep(PAUSE)
                try: run = get(f"/runs/shared/{row['run_hash']}")
                except OSError as exc:
                    print('skip', row['run_hash'], exc); continue
                path.write_text(json.dumps(run, ensure_ascii=False), encoding='utf-8')
                have.append(path)
            print(character, len(have), 'runs')
            page += 1
            time.sleep(PAUSE)


def catalog(kind):
    """Spire Codex card or relic texts in simplified Chinese, as the game shows them, by id."""
    rows = spire_codex._cached(f'{kind}-zhs', f'{API}/{kind}?lang=zhs', CATALOG_AGE,
                               lambda data: {r['id']: r for r in data if r.get('id')})
    if not rows: sys.exit(f'無法取得 Spire Codex 的 {kind} 資料（需要連網）')
    return rows


def bare(entity_id):
    return str(entity_id or '').split('.', 1)[-1]


def card_entry(card, cards, index=None):
    """A card as the game API reports it, from the run's {id, current_upgrade_level}."""
    info = cards.get(bare(card['id'])) or {}
    upgraded = (card.get('current_upgrade_level') or 0) > 0
    text = info.get('upgrade_description') if upgraded and info.get('upgrade_description') else info.get('description')
    cost = 'X' if info.get('is_x_cost') else info.get('cost')
    entry = {'id': bare(card['id']), 'name': (info.get('name') or bare(card['id'])) + ('+' if upgraded else ''),
             'type': info.get('type_key'), 'cost': None if cost is None else str(cost), 'star_cost': info.get('star_cost'),
             'description': spire_codex._plain(text), 'rarity': info.get('rarity_key'), 'is_upgraded': upgraded,
             'keywords': []}
    if index is not None: entry['index'] = index
    return entry


def points(run):
    """(floor, act number, map point) in order; floors count on across acts."""
    floor = 0
    for act, history in enumerate(run.get('map_point_history') or [], start=1):
        for point in history:
            floor += 1
            yield floor, act, point


def deck_before(run, player, floor):
    """The deck on reaching this floor: the final deck rewound through later removals,
    transforms and upgrades."""
    deck = [dict(c) for c in player.get('deck') or [] if (c.get('floor_added_to_deck') or 0) < floor]
    later = [s for f, _, point in points(run) if f >= floor for s in point.get('player_stats') or []]
    for stats in later:
        for change in stats.get('cards_transformed') or []:
            final, original = change.get('final_card') or {}, change.get('original_card') or {}
            match = next((c for c in deck if c['id'] == final.get('id')
                          and c.get('floor_added_to_deck') == final.get('floor_added_to_deck')), None)
            if match: deck.remove(match)
            if (original.get('floor_added_to_deck') or 0) < floor: deck.append(dict(original))
        for removed in stats.get('cards_removed') or []:
            if (removed.get('floor_added_to_deck') or 0) < floor: deck.append(dict(removed))
        for upgraded in stats.get('upgraded_cards') or []:
            match = max((c for c in deck if c['id'] == upgraded and (c.get('current_upgrade_level') or 0) > 0),
                        key=lambda c: c.get('current_upgrade_level') or 0, default=None)
            if match: match['current_upgrade_level'] -= 1
    return deck


def reward_spots(run):
    """Card rewards after fights: (floor, act, stats) with exactly three cards offered."""
    for floor, act, point in points(run):
        if not any(str(room.get('model_id') or '').startswith('ENCOUNTER.') for room in point.get('rooms') or []): continue
        for stats in point.get('player_stats') or []:
            if len(stats.get('card_choices') or []) == 3: yield floor, act, stats


def spot(path, run, floor, act, stats, cards, relics):
    """The card reward screen as the game API would show it, and the index the player picked (None: skip)."""
    player = run['players'][0]
    offered = [choice['card'] for choice in stats['card_choices']]
    relic_text = lambda r: relics.get(bare(r['id'])) or {}
    state = {
        'state_type': 'card_reward', 'game_mode': 'singleplayer',
        'card_reward': {'cards': [card_entry(c, cards, i) for i, c in enumerate(offered)], 'can_skip': True},
        'run': {'act': act, 'floor': floor, 'ascension': run.get('ascension')},
        'player': {'character_id': bare(player.get('character')), 'hp': stats.get('current_hp'),
                   'max_hp': stats.get('max_hp'), 'gold': stats.get('current_gold'),
                   'deck': [card_entry(c, cards) for c in deck_before(run, player, floor)],
                   'relics': [{'id': bare(r['id']), 'name': relic_text(r).get('name') or bare(r['id']),
                               'description': spire_codex._plain(relic_text(r).get('description'))}
                              for r in player.get('relics') or [] if (r.get('floor_added_to_deck') or 0) < floor]},
    }
    picked = next((i for i, choice in enumerate(stats['card_choices']) if choice.get('was_picked')), None)
    return {'id': f"{path.stem}#{floor}", 'won': bool(run.get('win')), 'state': state, 'picked': picked}


def spots(acts, count, seed, pattern='*-*.json'):
    """A fixed random sample of card rewards, the same for every config so results compare."""
    cards, relics = catalog('cards'), catalog('relics')
    found = []
    for path in sorted(RUNS.glob(pattern)):
        if pattern == '*-*.json' and '-win-' in path.name: continue  # The mixed sample stays as first drawn.
        run = json.loads(path.read_text(encoding='utf-8'))
        if len(run.get('players') or []) != 1: continue
        found += [spot(path, run, floor, act, stats, cards, relics)
                  for floor, act, stats in reward_spots(run) if act in acts]
    random.Random(seed).shuffle(found)
    return found[:count]


def chosen(decision):
    if decision['action'] != 'select_card_reward': return None
    return json.loads(decision['parameters_json'] or '{}').get('card_index')


def agree(config, acts, count, seed, workers, mode='top'):
    import regression
    import replay_eval
    replay_eval.engine.CARD_PICK = mode
    sample = spots(acts, count, seed)
    local, save, hits = regression.cached_calls(True)

    def ask(item):
        local.use = True
        try: return item, chosen(replay_eval.decide(config, item['state'])), None
        except Exception as exc: return item, None, str(exc)

    tally = Counter()
    try:
        with ThreadPoolExecutor(workers) as pool:
            for item, pick, error in pool.map(ask, sample):
                if error:
                    tally['error'] += 1; print('✗', item['id'], error[:120]); continue
                same = pick == item['picked']
                tally['asked'] += 1; tally['same'] += same
                tally['won'] += item['won']; tally['won_same'] += same and item['won']
                tally['model_skip'] += pick is None; tally['player_skip'] += item['picked'] is None
                tally['both_skip'] += pick is None and item['picked'] is None
                tally['player_took'] += item['picked'] is not None; tally['took_same'] += same and item['picked'] is not None
    finally:
        save()
    rate = lambda a, b: f'{a}/{b} = {a / b:.0%}' if b else '—'
    print(f"\n{config}：與 A10 玩家選擇一致 {rate(tally['same'], tally['asked'])}；勝場 {rate(tally['won_same'], tally['won'])}"
          f"（{mode}；錯誤 {tally['error']}；沿用快取 {hits[0]}）")
    print(f"跳過：模型 {tally['model_skip']} 次，其中玩家也跳過 {rate(tally['both_skip'], tally['model_skip'])}；"
          f"玩家跳過 {tally['player_skip']} 次，模型抓到 {rate(tally['both_skip'], tally['player_skip'])}")
    print(f"玩家有拿牌的 {tally['player_took']} 題：選到同一張 {rate(tally['took_same'], tally['player_took'])}")


def sweep(acts, count, seed):
    """Agreement of score-threshold skip rules, computed from the ratings alone."""
    from deck.advisor import advise
    rows = []
    for item in spots(acts, count, seed):
        ranked = advise(item['state']).get('ranked_cards') or []
        if not ranked: continue
        rows.append((ranked[0]['index'], ranked[0].get('score'), len(item['state']['player']['deck']), item['picked']))
    player_skip = sum(picked is None for *_, picked in rows)
    print(f'{len(rows)} 題，玩家跳過 {player_skip} 次')
    print('分數低於  牌組至少  一致率        跳過次數  跳對      抓到玩家跳過')
    for minimum in (0, 30, 35, 40, 45, 50, 55, 60):
        for deck in (0, 15, 20):
            if minimum == 0 and deck: continue
            same = skips = right = 0
            for top, score, size, picked in rows:
                pick = None if score is not None and score < minimum and size >= deck else top
                same += pick == picked; skips += pick is None; right += pick is None and picked is None
            print(f'{minimum:>8}  {deck:>8}  {same:>3}/{len(rows)} = {same / len(rows):>4.0%}  {skips:>8}  '
                  f'{right:>3} ({right / skips if skips else 0:.0%})  {right}/{player_skip}')


def act_rewards(character):
    """(run hash, act, offered ids, picked index or None, won) for every card reward, once per run."""
    seen = set()
    for path in sorted(RUNS.glob(f'{character}-*.json')):
        run_hash = path.stem.split('-')[-1]
        if run_hash in seen: continue  # A won run can be stored both in the mixed and the -win- set.
        seen.add(run_hash)
        run = json.loads(path.read_text(encoding='utf-8'))
        if len(run.get('players') or []) != 1: continue
        for _, act, stats in reward_spots(run):
            choices = stats['card_choices']
            yield (run_hash, act, [bare(c['card']['id']) for c in choices],
                   next((i for i, c in enumerate(choices) if c.get('was_picked')), None), bool(run.get('win')))


def count_picks(rewards):
    rows = {}
    for _, act, offered, picked, _ in rewards:
        for i, card in enumerate(offered):
            counts = rows.setdefault(str(act), {}).setdefault(card, [0, 0])
            counts[0] += 1; counts[1] += picked == i
    return rows


def pickrates(characters, folds=5):
    """Write the per-act pick-rate table takeover uses, after checking it out of sample by run."""
    import zlib
    from deck import pick_rates
    out = {'built_at': time.strftime('%Y-%m-%d'), 'characters': {}}
    for character in characters:
        rewards = list(act_rewards(character))
        if not rewards: print(character, '沒有對局，先 fetch'); continue
        for act in sorted({r[1] for r in rewards}):
            tally = Counter()
            for k in range(folds):
                fold = lambda r: zlib.crc32(r[0].encode()) % folds == k
                pick_rates._table = {'characters': {character: count_picks([r for r in rewards if not fold(r)])}}
                for _, act_, offered, picked, _ in (r for r in rewards if fold(r) and r[1] == act):
                    state = {'state_type': 'card_reward', 'player': {'character_id': character}, 'run': {'act': act_},
                             'card_reward': {'can_skip': True, 'cards': [{'index': i, 'id': c} for i, c in enumerate(offered)]}}
                    choice = pick_rates.popular_pick(state)
                    tally['n'] += 1; tally['skip'] += picked is None
                    tally['same'] += (None if choice.get('skip') else choice['index']) == picked
            print(f"{character} 第 {act} 幕：{tally['n']} 題，玩家跳過 {tally['skip']}；選取率規則與玩家一致 "
                  f"{tally['same'] / tally['n']:.1%}（{folds} 折，依對局切分）")
        out['characters'][character] = count_picks(rewards)
    pick_rates._table = None
    pick_rates.PATH.parent.mkdir(parents=True, exist_ok=True)
    pick_rates.PATH.write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
    print('寫入', pick_rates.PATH)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('fetch')
    p.add_argument('--characters', nargs='+', default=['IRONCLAD', 'DEFECT'])
    p.add_argument('--runs', type=int, default=150, help='runs per character')
    p.add_argument('--ascension', type=int, default=10)
    p.add_argument('--wins', action='store_true', help='only won runs (kept apart as <CHARACTER>-win-<hash>)')
    p = sub.add_parser('agree')
    p.add_argument('--config', default='claude:haiku@low')
    p.add_argument('--spots', type=int, default=60)
    p.add_argument('--acts', type=int, nargs='+', default=[1])
    p.add_argument('--seed', type=int, default=7)
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--mode', choices=['top', 'rating', 'free'], default='top',
                   help='top: best-rated card, no model; rating: model takes or skips it; free: model chooses')
    p = sub.add_parser('pickrates', help='write the per-act pick-rate table takeover picks card rewards with')
    p.add_argument('--characters', nargs='+', default=['IRONCLAD', 'DEFECT'])
    p = sub.add_parser('sweep')
    p.add_argument('--spots', type=int, default=300)
    p.add_argument('--acts', type=int, nargs='+', default=[1])
    p.add_argument('--seed', type=int, default=7)
    args = parser.parse_args()
    if args.command == 'fetch': fetch(args.characters, args.runs, args.ascension, args.wins)
    elif args.command == 'pickrates': pickrates(args.characters)
    elif args.command == 'sweep': sweep(set(args.acts), args.spots, args.seed)
    else: agree(args.config, set(args.acts), args.spots, args.seed, args.workers, args.mode)


if __name__ == '__main__': main()
