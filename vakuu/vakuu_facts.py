"""Numbers computed for the model so it does not have to: combat, energy, deck and route facts."""
import re
from pathlib import Path

COMMON_INTENTS = {'Attack', 'Defend', 'Buff', 'Debuff', 'Sleep', 'Unknown'}

def deals_damage(intent):
    # Not only "Attack": the Waterfall Giant's DeathBlow ("濒死一击") hits for 53 when it dies, and
    # counting it as 0 told the model no block was needed, which killed the run.
    kind = str(intent.get('type'))
    text = str(intent.get('description') or '')
    return 'Attack' in kind or 'DeathBlow' in kind or '伤害' in text or '傷害' in text

def incoming_damage(intent):
    if not deals_damage(intent): return 0
    numbers = [int(n) for n in re.findall(r'\d+', str(intent.get('label') or ''))]
    if not numbers: return 0
    return numbers[0] * (numbers[1] if len(numbers) > 1 else 1)  # "12×2" means two hits of 12.

def combat_facts(state):
    """Arithmetic the model kept getting wrong, computed once from the live state."""
    player = state.get('player') or {}
    enemies = [e for e in (state.get('battle') or {}).get('enemies') or [] if (e.get('hp') or 0) > 0]
    incoming = sum(incoming_damage(i) for e in enemies for i in e.get('intents') or [])
    block = player.get('block') or 0
    hp = player.get('hp') or 0
    loss = max(0, incoming - block)
    return {
        'incoming_attack_damage': incoming,
        # Unusual intents carry their own rules (timing, conditions); the model must read them itself.
        'special_intents': [{'enemy': e.get('name'), 'type': i.get('type'), 'label': i.get('label'),
                             'description': i.get('description')}
                            for e in enemies for i in e.get('intents') or []
                            if not any(kind in str(i.get('type')) for kind in COMMON_INTENTS)],
        'current_block': block,
        'block_needed_for_no_damage': loss,
        'hp_after_enemy_attacks_if_no_more_block': hp - loss,
        'lethal_if_no_more_block': loss >= hp > 0,
        'energy': player.get('energy'),
        'playable_cards': [{'index': c.get('index'), 'name': c.get('name'), 'cost': c.get('cost'),
                            'gives_energy_now': gives_energy_now(c), 'exhausts': '消耗' in str(c.get('description') or '')}
                           for c in player.get('hand') or [] if c.get('can_play')],
        # >= 0: current energy already pays for every other card in hand, so energy cards would be wasted.
        'spare_energy_without_energy_cards': spare_energy(state),
        'enemies': [{'entity_id': e.get('entity_id'), 'name': e.get('name'), 'hp': e.get('hp'), 'block': e.get('block')}
                    for e in enemies],
        'note': '傷害已含意圖顯示的修正；未計回合結束時的被動格擋或傷害（如冰霜、閃電充能球）。',
    }

def deck_summary(deck):
    """One entry per distinct card with a count, so the model knows what each card does without repeats."""
    groups = {}
    for card in deck:
        key = (card.get('id'), card.get('name'), card.get('is_upgraded'))
        if key in groups: groups[key]['count'] += 1
        else: groups[key] = {'name': card.get('name'), 'count': 1, 'cost': card.get('cost'),
                             'upgraded': bool(card.get('is_upgraded')), 'description': card.get('description')}
    return list(groups.values())

ENERGY_NOW = re.compile(r'^[获獲]得\s*\d*\s*\[[^\]]*energy')

def gives_energy_now(card):
    # Only cards whose effect *is* gaining energy now, like Supercritical ("获得4[energy]。 消耗。").
    # Not next turn's energy ("在下个回合获得..."), and not attacks with an energy bonus: Sever
    # ("造成26点伤害。 如果这张牌击杀了敌人，则获得3[energy]") was once skipped as a wasted energy
    # card, leaving an enemy alive that it would have killed.
    text = str(card.get('description') or '').strip()
    if card.get('type') == 'Attack' or '如果' in text or '下个回合' in text or '下回合' in text: return False
    return bool(ENERGY_NOW.search(text))

def card_cost(card):
    try: return int(card.get('cost'))
    except (TypeError, ValueError): return 0  # X costs and unknowns: treat as free here.

def spare_energy(state):
    """Energy left after paying for every other card that could be played this turn (negative: short)."""
    player = state.get('player') or {}
    wanted = [c for c in player.get('hand') or []
              if (c.get('can_play') or c.get('unplayable_reason') == 'EnergyCostTooHigh') and not gives_energy_now(c)]
    return (player.get('energy') or 0) - sum(card_cost(c) for c in wanted)

def wastes_energy_card(state, card):
    """Playing an energy card while the current energy already covers the whole hand throws it away,
    and cards like Supercritical exhaust, so it is gone for the rest of the fight."""
    return gives_energy_now(card) and spare_energy(state) >= 0

ROUTE_COUNTS = {'elites': ('Elite',), 'fights': ('Monster', 'Elite'), 'rests': ('RestSite',),
                'shops': ('Shop',), 'unknowns': ('Unknown',), 'treasures': ('Treasure',)}

MAX_PATHS = 20000  # Act maps have hundreds to a few thousand paths; stop enumerating beyond this.
ROUTE_STYLES = {
    # name: sort key over a path's counts (smaller is better)
    # Guides call skipping every elite weak (elites give the relics that win runs): take 1-2 per act
    # when a rest site follows within two rooms.
    'balanced': lambda c: (not 1 <= c['elites'] <= 2, c['elites'] - c['elites_rest_after'], -c['rests'], c['fights']),
    'safest': lambda c: (c['elites'], c['fights'], -c['rests']),
    'most_rest': lambda c: (-c['rests'], c['elites'], c['fights']),
    'most_shops': lambda c: (-c['shops'], c['elites'], -c['rests']),
    'elite_hunt': lambda c: (-c['elites'], -c['rests'], c['fights']),
}

def candidate_routes(world, nodes, children):
    """Whole routes to the act's end, one per style, so the model picks a full path.

    Per-option counts hid that a chosen path ran into a forced elite right after its rest site:
    the option's best path stayed elite-free for 11 rooms, the path actually taken for only 4.
    """
    paths = []
    def walk(key, path):
        if len(paths) >= MAX_PATHS: return
        path = path + [key]
        below = children(key)
        if not below: paths.append(path)
        for child in below: walk(child, path)
    starts = {(o.get('col'), o.get('row')): o.get('index') for o in world.get('next_options') or []}
    for start in starts:
        if start in nodes: walk(start, [])
    summaries = []
    for path in paths:
        kinds = [nodes[k].get('type') for k in path]
        counts = {name: sum(kind in types for kind in kinds) for name, types in ROUTE_COUNTS.items()}
        counts['elites_rest_after'] = sum(1 for i, kind in enumerate(kinds)
                                          if kind == 'Elite' and 'RestSite' in kinds[i + 1:i + 3])
        summaries.append((path, kinds, counts))
    routes, seen = [], set()
    for style, key in ROUTE_STYLES.items():
        if not summaries: break
        path, kinds, counts = min(summaries, key=lambda item: key(item[2]))
        if tuple(path) in seen: continue
        seen.add(tuple(path))
        routes.append({'style': style, 'option_index': starts[path[0]], 'counts': counts,
                       'first_elite_at_room': next((i + 1 for i, kind in enumerate(kinds) if kind == 'Elite'), None),
                       'rooms': [{'col': c, 'row': r, 'type': t} for (c, r), t in zip(path, kinds)]})
    return routes

def route_facts(state):
    """Per route option: what every path from it holds, so the model need not trace the node graph.

    The map arrives as ~60 nodes with child coordinates; the model kept judging only the next step,
    and an elite taken at 43% HP ended a run.
    """
    world = state.get('map') or {}
    nodes = {(n['col'], n['row']): n for n in world.get('nodes') or [] if 'col' in n and 'row' in n}
    children = lambda key: [tuple(c) for c in nodes[key].get('children') or [] if tuple(c) in nodes]
    totals, rests, calm = {}, {}, {}

    def elite_free(key):  # Most rooms a path from key can go before its first elite (whole act if none).
        if key not in calm:
            below = [elite_free(child) for child in children(key)]
            calm[key] = 0 if nodes[key].get('type') == 'Elite' else 1 + (max(below) if below else 0)
        return calm[key]

    def to_end(key):  # (min, max) of each count over all paths from key to the act's end, inclusive.
        if key not in totals:
            kind = nodes[key].get('type')
            own = {name: int(kind in kinds) for name, kinds in ROUTE_COUNTS.items()}
            below = [to_end(child) for child in children(key)]
            totals[key] = {name: [own[name] + (min(b[name][0] for b in below) if below else 0),
                                  own[name] + (max(b[name][1] for b in below) if below else 0)] for name in own}
        return totals[key]

    def to_rest(key):  # Steps and fights before the first rest site on any path, or None if unreachable.
        if key not in rests:
            node = nodes[key]
            if node.get('type') == 'RestSite':
                rests[key] = {'steps': 0, 'elites': [0, 0], 'fights': [0, 0]}
            else:
                below = [r for r in (to_rest(child) for child in children(key)) if r]
                elite, fight = int(node.get('type') == 'Elite'), int(node.get('type') in ('Monster', 'Elite'))
                rests[key] = None if not below else {
                    'steps': 1 + min(r['steps'] for r in below),
                    'elites': [elite + min(r['elites'][0] for r in below), elite + max(r['elites'][1] for r in below)],
                    'fights': [fight + min(r['fights'][0] for r in below), fight + max(r['fights'][1] for r in below)]}
        return rests[key]

    player = state.get('player') or {}
    facts = {'hp_ratio': round((player.get('hp') or 0) / (player.get('max_hp') or 1), 2), 'options': [],
             'candidate_routes': candidate_routes(world, nodes, children)}
    for option in world.get('next_options') or []:
        key = (option.get('col'), option.get('row'))
        if key not in nodes: continue
        facts['options'].append({'index': option.get('index'), 'type': option.get('type'),
                                 'until_next_rest_site': to_rest(key), 'to_act_end_min_max': to_end(key),
                                 # An elite can be forced several rooms later (a single path after a rest site).
                                 'max_rooms_before_first_elite': elite_free(key)})
    return facts

REST_HEAL = 0.3  # Resting heals 30% of max HP.

def rest_facts(state, route):
    """Numbers for rest-or-upgrade: guides default to upgrading and rest below ~40% HP before danger."""
    player = state.get('player') or {}
    hp, max_hp = player.get('hp') or 0, player.get('max_hp') or 1
    heal = min(int(max_hp * REST_HEAL), max_hp - hp)
    upcoming = [route['types'].get(node) for node in route['path'][:4]] if route and route.get('types') else None
    return {'hp_ratio': round(hp / max_hp, 2), 'rest_heals': heal, 'hp_after_rest': hp + heal,
            'planned_next_rooms': upcoming}

ENEMY_NOTES = Path(__file__).resolve().parent / 'enemy_notes.md'
NOTE_LINE = re.compile(r'^-\s*([A-Z0-9_]+)\s*[：:]\s*(.+)$')

def enemy_notes(state):
    """Player-written tactics for the enemies in this fight (models misjudged some, e.g. Corpse Slugs).

    Read on every call from enemy_notes.md, so a note added mid-run applies to the next decision.
    """
    try: lines = ENEMY_NOTES.read_text(encoding='utf-8').splitlines()
    except OSError: return {}
    notes = dict(m.groups() for m in map(NOTE_LINE.match, lines) if m)
    return {enemy: notes[enemy] for enemy in enemy_ids(state) if enemy in notes}

def enemy_ids(state):
    """Distinct monster ids in the fight (entity ids without the _0, _1 suffix)."""
    return sorted({re.sub(r'_\d+$', '', str(e.get('entity_id') or '')) for e in (state.get('battle') or {}).get('enemies') or []} - {''})

BLOCK_TEXT = re.compile(r'[获獲]得\s*(\d+)\s*点?[格]挡')
STARTERS = ('STRIKE', 'DEFEND')

def deck_profile(deck):
    """What the deck has and lacks, so card choices consider needs and not only general card ratings.

    A run died with 4 Strikes and 4 Defends after skipping block cards rated "F": ratings say how strong
    a card is in general, not whether this deck is short of block.
    """
    count = lambda pred: sum(1 for card in deck if pred(card))
    text = lambda card: str(card.get('description') or '')
    return {
        'size': len(deck),
        'attacks': count(lambda c: c.get('type') == 'Attack'),
        'skills': count(lambda c: c.get('type') == 'Skill'),
        'powers': count(lambda c: c.get('type') == 'Power'),
        'block_sources': count(lambda c: BLOCK_TEXT.search(text(c)) or '冰霜' in text(c)),
        'block_sources_besides_defend': count(lambda c: (BLOCK_TEXT.search(text(c)) or '冰霜' in text(c))
                                               and not str(c.get('id', '')).startswith('DEFEND')),
        'starter_strikes': count(lambda c: str(c.get('id', '')).startswith('STRIKE')),
        'starter_defends': count(lambda c: str(c.get('id', '')).startswith('DEFEND')),
        'unplayable': count(lambda c: '不能被打出' in text(c)),
    }

