"""Following the route the model drew, and resting when the forecast says so."""
import json

from core.rules import program_move
from route.facts import project_route, rest_facts, route_damage

# Re-plan the route when HP fell this much (fraction of max HP) since it was drawn,
# or fell this far below the HP projected for the current room.
ROUTE_HP_DROP = 0.2

ROUTE_BEHIND = 0.15

def hp_ratio(state):
    player = state.get('player') or {}
    return (player.get('hp') or 0) / (player.get('max_hp') or 1)

def start_route(state, decision):
    """Remember the route the model drew, like a player planning the act once instead of per node."""
    if state.get('state_type') != 'map' or decision.get('action') != 'choose_map_node': return None
    path = [(node['col'], node['row']) for node in decision.get('next_nodes') or []
            if isinstance(node, dict) and isinstance(node.get('col'), int) and isinstance(node.get('row'), int)]
    if not path: return None
    kinds = {(n.get('col'), n.get('row')): n.get('type') for n in (state.get('map') or {}).get('nodes') or []}
    # Expected HP at each room of the chosen route, to notice when the run falls behind the plan.
    options = {o.get('index'): (o.get('col'), o.get('row')) for o in (state.get('map') or {}).get('next_options') or []}
    first = options.get(json.loads(decision['parameters_json']).get('index'))
    if first:
        # The model often repeats the chosen node as the first of next_nodes; left in, the rest site
        # being entered counted as the next one ahead, and the projection saw nothing to rest for.
        path = [node for node in path if node[1] > first[1]]
    walk = ([first] if first else []) + path
    player = state.get('player') or {}
    curve = project_route([{'type': kinds.get(node), 'row': node[1]} for node in walk],
                          player.get('hp') or 0, player.get('max_hp') or 1, route_damage(state))['hp_by_room']
    assumed = safest_tail(state.get('map') or {}, walk[-1])
    return {'act': (state.get('run') or {}).get('act'), 'hp_ratio': hp_ratio(state), 'path': path,
            'assumed': assumed, 'types': {node: kinds.get(node) for node in walk + assumed},
            'expected_hp': dict(zip(walk, curve)), 'max_hp': player.get('max_hp') or 1}

def safest_tail(world, last):
    """The rooms after the drawn route's last one, on the safest way to the boss.

    The model drew a route that ended at a rest site; at that rest site the program saw no rooms ahead,
    projected upgrading as free, and the player upgraded at 41/80 into the only next room, an elite.
    The tail is only assumed for projections: takeover still asks the model at the next fork.
    """
    nodes = {(n['col'], n['row']): n for n in world.get('nodes') or [] if 'col' in n and 'row' in n}
    children = lambda key: [tuple(c) for c in (nodes.get(key) or {}).get('children') or [] if tuple(c) in nodes]
    best = {}

    def cost(key):  # (elites, fights, -rests) from key to the act's end, and the path that achieves it.
        if key not in best:
            kind = nodes[key].get('type')
            own = (int(kind == 'Elite'), int(kind in ('Monster', 'Elite')), -int(kind == 'RestSite'))
            tails = [cost(child) for child in children(key)]
            score, path = min(tails, key=lambda tail: tail[0]) if tails else ((0, 0, 0), [])
            best[key] = (tuple(a + b for a, b in zip(own, score)), [key] + path)
        return best[key]

    if last not in nodes: return []
    tails = [cost(child) for child in children(last)]
    return min(tails, key=lambda tail: tail[0])[1] if tails else []

def rest_decision(route, state):
    """Rest without asking when upgrading would leave the run below 30% HP before the next rest site.

    The model was told so and still upgraded at 43/75 ("the next room is a rest site"; it was an
    elite), then died in that elite. The projection is plain arithmetic, so the program follows it.
    """
    if state.get('state_type') != 'rest_site': return None
    facts = rest_facts(state, route)
    if not facts.get('rest_recommended'): return None
    heal = next((o for o in (state.get('rest_site') or {}).get('options') or []
                 if o.get('id') == 'HEAL' and o.get('is_enabled', True)), None)
    if heal is None: return None
    return program_move(f"照路線預估，升級的話到下一個休息點前會跌到 {facts['lowest_hp_before_next_rest_if_upgrade']} 血。"
                 '\n建議：休息回血。', 'choose_rest_option', index=heal['index'])

def route_decision(route, state):
    """Next node of the planned route; None asks the model to re-plan (route left, big HP loss, new act)."""
    if not route or state.get('state_type') != 'map': return None
    if (state.get('run') or {}).get('act') != route['act']: return None
    if route['hp_ratio'] - hp_ratio(state) >= ROUTE_HP_DROP: return None
    world = state.get('map') or {}
    here = world.get('current_position') or {}
    row = here.get('row', -1)
    # Rest sites and shops are where a player rethinks the route; and falling well behind the
    # projected HP means the plan's assumptions no longer hold.
    if here.get('type') in ('RestSite', 'Shop'): return None
    expected = (route.get('expected_hp') or {}).get((here.get('col'), here.get('row')))
    if expected is not None and expected - ((state.get('player') or {}).get('hp') or 0) >= ROUTE_BEHIND * route.get('max_hp', 1):
        return None
    route['path'] = [node for node in route['path'] if node[1] > row]  # Drop rooms already passed.
    route['assumed'] = [node for node in route.get('assumed') or [] if node[1] > row]
    if not route['path']: return None
    col, next_row = route['path'][0]
    # Entering an elite is decided with the HP and deck of that moment, not the plan's: a route drawn
    # at 5 HP ("rest, then decide on the elite") walked into the elite at 36% HP after resting and died.
    if (route.get('types') or {}).get((col, next_row)) == 'Elite': return None
    option = next((o for o in world.get('next_options') or [] if (o.get('col'), o.get('row')) == (col, next_row)), None)
    if option is None: return None
    route['path'].pop(0)
    route['assumed'] = [node for node in route.get('assumed') or [] if node[1] > next_row]
    return program_move('照既定路線前進。\n建議：前往路線上的下一個地點。', 'choose_map_node', index=option['index'])
