"""Route projections for the map screen and the rest-site HP forecast."""
from core import spire_codex

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

# Fallback damage per room when Spire Codex has no data (roughly Act 1 averages for Defect at A10+).
DEFAULT_DAMAGE = {'monster_weak': 8, 'monster': 14, 'elite': 28, 'boss': 45}

UNKNOWN_FIGHT_CHANCE = 0.3  # A ? room is sometimes a fight; counted as that share of a normal fight.

WEAK_ROWS = 3  # Each act's first fights draw from the weak encounter pool.

def project_route(rooms, hp, max_hp, damage):
    """Walk a route with community average HP loss per room; rest sites heal 30% only below 40% HP.

    A route with six fights before its first rest site looked fine by room counts and lost a run;
    projecting HP shows where it bottoms out.
    """
    curve, first_rest = [], None
    for room in rooms:
        kind, row = room['type'], room['row']
        if kind == 'Boss': break
        if kind == 'Monster': hp -= damage['monster_weak'] if row <= WEAK_ROWS else damage['monster']
        elif kind == 'Elite': hp -= damage['elite']
        elif kind == 'Unknown': hp -= UNKNOWN_FIGHT_CHANCE * damage['monster']
        elif kind == 'RestSite':
            if first_rest is None: first_rest = len(curve)
            if hp < 0.4 * max_hp: hp = min(max_hp, hp + REST_HEAL * max_hp)
        curve.append(round(hp))
    lowest = min(curve) if curve else round(hp)
    danger = next((i + 1 for i, value in enumerate(curve) if value < 0.25 * max_hp), None)
    # Averages ignore the deck improving, events and potions, so whole-act projections run low on
    # every route; the stretch before the first rest site (re-planned there) compares routes best.
    before_rest = curve[:first_rest] if first_rest is not None else curve
    return {'lowest_before_first_rest': min(before_rest) if before_rest else round(hp),
            'hp_by_room': curve, 'lowest_hp': lowest, 'lowest_at_room': curve.index(lowest) + 1 if curve else None,
            'hp_before_boss': curve[-1] if curve else round(hp), 'below_25pct_at_room': danger,
            # Run 28 took the last elite before the act 2 boss at 59/75: elite, rest, then a boss that
            # costs 59.5 HP on average left nothing to spare, and a bad elite fight lost the run.
            'boss_average_damage': round(damage['boss']),
            'hp_after_boss_if_average': round((curve[-1] if curve else hp) - damage['boss'])}

BOSS_NEAR_ROOMS = 6  # Rooms left before the boss at which projections are trusted for the last elite.

BOSS_SPARE = 0.2  # HP share an average boss fight should leave for the last elite to be worth it.

def route_damage(state):
    player = state.get('player') or {}
    found = spire_codex.room_damage(player.get('character_id') or '', (state.get('run') or {}).get('act'))
    return {**DEFAULT_DAMAGE, **(found or {})}

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
    damage = route_damage(state)
    routes = candidate_routes(world, nodes, children)
    for route in routes:
        route['projected'] = project_route(route['rooms'], player.get('hp') or 0, player.get('max_hp') or 1, damage)
        # Close to the boss the averages are reliable enough to call an elite a bad bet outright.
        before_boss = [r for r in route['rooms'] if r['type'] != 'Boss']
        risky = route['projected']['hp_after_boss_if_average'] < BOSS_SPARE * (player.get('max_hp') or 1)
        if len(before_boss) <= BOSS_NEAR_ROOMS and any(r['type'] == 'Elite' for r in before_boss) and risky:
            route['elite_too_risky_before_boss'] = True
    facts = {'hp_ratio': round((player.get('hp') or 0) / (player.get('max_hp') or 1), 2), 'options': [],
             'expected_damage_per_room': damage, 'candidate_routes': routes}
    for option in world.get('next_options') or []:
        key = (option.get('col'), option.get('row'))
        if key not in nodes: continue
        facts['options'].append({'index': option.get('index'), 'type': option.get('type'),
                                 'until_next_rest_site': to_rest(key), 'to_act_end_min_max': to_end(key),
                                 # An elite can be forced several rooms later (a single path after a rest site).
                                 'max_rooms_before_first_elite': elite_free(key)})
    return facts

REST_HEAL = 0.3  # Resting heals 30% of max HP.

REST_FLOOR = 0.3  # Rest when upgrading would leave less than this share of max HP before the next rest site.

def ahead_nodes(route, state=None):
    """The drawn route, then the assumed safest way on to the boss where the drawing stopped short.

    Only rooms past the current floor: a move the program makes alone (the only way on) does not walk
    the route, and an Ironclad run upgraded before its act 1 boss because the room just cleared still
    counted as the next one ahead.
    """
    nodes = list(route.get('path') or []) + list(route.get('assumed') or [])
    floor = ((state or {}).get('run') or {}).get('floor')
    if isinstance(floor, int) and isinstance(route.get('row_floor'), int):
        nodes = [node for node in nodes if node[1] + route['row_floor'] > floor]
    return nodes

# Before a boss, rest when an average boss fight would leave less than this share of max HP after
# upgrading. Lower than REST_FLOOR: the act ends with a heal, so the boss only has to be survived.
BOSS_REST_FLOOR = 0.15

def rest_facts(state, route):
    """Numbers for rest-or-upgrade, with the HP projected to the next rest site if this one upgrades.

    Two overnight runs upgraded at 52% HP in act 1 ("still above 40%"), lost 28 HP in the next fight
    and died a room later; the projection shows when the stretch ahead cannot be survived on average.
    """
    player = state.get('player') or {}
    hp, max_hp = player.get('hp') or 0, player.get('max_hp') or 1
    heal = min(int(max_hp * REST_HEAL), max_hp - hp)
    facts = {'hp_ratio': round(hp / max_hp, 2), 'rest_heals': heal, 'hp_after_rest': hp + heal}
    if not route or not route.get('types'): return {**facts, 'planned_next_rooms': None}
    ahead = ahead_nodes(route, state)
    rooms, boss_next = [], False
    for node in ahead:
        kind = route['types'].get(node)
        if kind in ('RestSite', 'Boss'):
            boss_next = kind == 'Boss'
            break
        rooms.append({'type': kind, 'row': node[1]})
    damage = route_damage(state)
    walk = lambda start: project_route(rooms, start, max_hp, damage)['hp_by_room'] or [start]
    lowest = lambda start: min(walk(start))
    if_upgrade, if_rest = lowest(hp), lowest(hp + heal)
    facts.update({'planned_next_rooms': [route['types'].get(node) for node in ahead[:4]],
                  'rooms_before_next_rest_site': [r['type'] for r in rooms],
                  'lowest_hp_before_next_rest_if_upgrade': round(if_upgrade),
                  'lowest_hp_before_next_rest_if_rest': round(if_rest),
                  'rest_recommended': if_upgrade < REST_FLOOR * max_hp})
    if boss_next:
        # Two Ironclad runs upgraded at 40/80 before a boss that costs 51 HP on average and died in it:
        # with no rest site before the boss, the projection stopped short of the fight that mattered.
        after_upgrade, after_rest = walk(hp)[-1] - damage['boss'], walk(hp + heal)[-1] - damage['boss']
        facts.update({'boss_next': True, 'boss_average_damage': round(damage['boss']),
                      'hp_after_boss_if_upgrade': round(after_upgrade), 'hp_after_boss_if_rest': round(after_rest),
                      'rest_recommended': facts['rest_recommended'] or after_upgrade < BOSS_REST_FLOOR * max_hp})
    return facts

def route_ahead(route, state=None):
    """The planned rooms after this one, and how far the next rest site, shop and elite are.

    Event options trade HP or gold; whether 7 HP for 101 gold is worth it depends on the next rest
    site and shop, which the event screen does not show.
    """
    if not route or not route.get('types'): return None
    rooms = [route['types'].get(node) for node in ahead_nodes(route, state)]
    if not rooms: return None
    distance = lambda kind: next((i + 1 for i, room in enumerate(rooms) if room == kind), None)
    return {'next_rooms': rooms[:8], 'rooms_to_rest_site': distance('RestSite'), 'rooms_to_shop': distance('Shop'),
            'rooms_to_elite': distance('Elite'), 'rooms_to_boss': len(rooms) + 1}
