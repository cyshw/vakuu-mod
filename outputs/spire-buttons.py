"""Vakuu takeover engine and the HTTP bridge used by the in-game panel."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('game_api', ROOT / 'sts2-control.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
from sts2_advisor import advise
import spire_models as models
import spire_quota

# Tuned for the game's Instant fast mode; wait_until_settled covers screens that still animate.
ACTION_PAUSE = 0.5
POLL_INTERVAL = 0.25

FIELDS = {
    'play_card': {'card_index', 'target'}, 'end_turn': set(),
    'use_potion': {'slot', 'target'}, 'discard_potion': {'slot'},
    'combat_select_card': {'card_index'}, 'combat_confirm_selection': set(),
    'claim_reward': {'index'}, 'proceed': set(),
    'select_card_reward': {'card_index'}, 'skip_card_reward': set(),
    'choose_map_node': {'index'}, 'choose_event_option': {'index'}, 'advance_dialogue': set(),
    'choose_rest_option': {'index'}, 'shop_purchase': {'index'},
    'claim_treasure_relic': {'index'}, 'select_card': {'index'},
    'confirm_selection': set(), 'cancel_selection': set(),
    'select_bundle': {'index'}, 'confirm_bundle_selection': set(), 'cancel_bundle_selection': set(),
    'select_relic': {'index'}, 'skip_relic_selection': set(),
}
ALLOWED = {
    'monster': {'play_card', 'end_turn'}, 'elite': {'play_card', 'end_turn'},
    'boss': {'play_card', 'end_turn'},
    'hand_select': {'combat_select_card', 'combat_confirm_selection'},
    'rewards': {'claim_reward', 'proceed'},
    'card_reward': {'select_card_reward', 'skip_card_reward'},
    'map': {'choose_map_node'}, 'event': {'choose_event_option', 'advance_dialogue'},
    'rest_site': {'choose_rest_option', 'proceed'},
    'shop': {'shop_purchase', 'proceed'}, 'fake_merchant': {'shop_purchase', 'proceed'},
    'treasure': {'claim_treasure_relic', 'proceed'},
    'card_select': {'select_card', 'confirm_selection', 'cancel_selection'},
    'bundle_select': {'select_bundle', 'confirm_bundle_selection', 'cancel_bundle_selection'},
    'relic_select': {'select_relic', 'skip_relic_selection'},
}
SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'advice': {'type': 'string'}, 'stop': {'type': 'boolean'},
        'action': {'type': 'string', 'enum': ['none'] + list(FIELDS)},
        'parameters_json': {'type': 'string'},
        # Rest of this turn after `action`, so the turn is played as one coherent plan.
        'next_cards': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'hand_index': {'type': 'integer'}, 'target': {'type': 'string'}},
            'required': ['hand_index', 'target']}},
        # Shop items to buy after `action`, so a shop visit is planned in one call.
        'next_purchases': {'type': 'array', 'items': {'type': 'integer'}},
        # A per-fight battle plan, written only when the prompt asks for one (auto:brief).
        'strategy': {'type': 'string'},
    }, 'required': ['advice', 'stop', 'action', 'parameters_json', 'next_cards', 'next_purchases', 'strategy']
}
COMBAT = {'monster', 'elite', 'boss'}
END_TURN = -1  # hand_index that means "end the turn" in next_cards.
LEAVE_SHOP = -1  # Item index that means "leave the shop" in next_purchases.

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

def hand_names(state):
    return Counter(c.get('name') for c in (state.get('player') or {}).get('hand') or [])

def start_plan(state, decision):
    if state.get('state_type') == 'shop': return start_shop_plan(state, decision)
    return start_turn_plan(state, decision)

def start_shop_plan(state, decision):
    """Remember the rest of the shopping list; item indices stay stable as items sell out."""
    if decision['action'] != 'shop_purchase': return None
    queue = [i for i in decision.get('next_purchases') or [] if isinstance(i, int)]
    if LEAVE_SHOP in queue: queue = queue[:queue.index(LEAVE_SHOP) + 1]
    return {'kind': 'shop', 'queue': queue} if queue else None

def shop_plan_decision(plan, state):
    if state.get('state_type') != 'shop': return None
    index = plan['queue'].pop(0)
    if index == LEAVE_SHOP:
        return _auto('該買的都買了，走吧。\n建議：離開商店。', 'proceed')
    item = next((i for i in (state.get('shop') or {}).get('items') or [] if i.get('index') == index), None)
    if not item or not item.get('is_stocked') or not item.get('can_afford'): return None
    name = item.get('card_name') or item.get('relic_name') or item.get('potion_name') or '刪牌服務'
    return _auto('照方才的盤算，買下它。\n建議：購買' + name + '。', 'shop_purchase', index=index)

def start_turn_plan(state, decision):
    """Remember the model's remaining moves for this turn, with the hand we expect afterwards."""
    if state.get('state_type') not in COMBAT or decision['action'] not in ('play_card', 'end_turn') \
            or not decision.get('next_cards'):
        return None
    # The model refers to cards by their index in the hand it was shown; store the game's exact names,
    # since the model rewrites names (e.g. simplified to traditional Chinese) and indices shift as cards are played.
    hand = (state.get('player') or {}).get('hand') or []
    queue = []
    for entry in decision.get('next_cards') or []:
        index = entry.get('hand_index')
        if index == END_TURN: queue.append({'name': END_TURN, 'target': ''}); break
        if not isinstance(index, int) or not 0 <= index < len(hand): break
        queue.append({'name': hand[index].get('name'), 'target': entry.get('target') or ''})
    if not queue: return None
    expected = hand_names(state)
    if decision['action'] == 'play_card':
        index = json.loads(decision['parameters_json']).get('card_index')
        if not isinstance(index, int) or not 0 <= index < len(hand): return None
        expected[hand[index].get('name')] -= 1
    return {'kind': 'turn', 'round': state['battle'].get('round'), 'queue': queue, 'expected': expected}

ENERGY_NOW = re.compile(r'[获獲]得\s*\d*\s*\[[^\]]*energy')

def gives_energy_now(card):
    # "获得4[energy]" (Supercritical) pays off this turn; "在下个回合获得..." (Turbo-like) is next turn's budget.
    text = str(card.get('description') or '')
    return bool(ENERGY_NOW.search(text)) and '下个回合' not in text and '下回合' not in text

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

def first_card(state, decision):
    if decision.get('action') != 'play_card': return None
    try: index = json.loads(decision['parameters_json']).get('card_index')
    except (ValueError, AttributeError): return None
    hand = (state.get('player') or {}).get('hand') or []
    return hand[index] if isinstance(index, int) and 0 <= index < len(hand) else None

def plan_decision(plan, state):
    """Next planned move if things went as the model expected; None means ask the model again."""
    if not plan or not plan['queue']: return None
    if plan['kind'] == 'shop': return shop_plan_decision(plan, state)
    if state.get('state_type') not in COMBAT or (state.get('battle') or {}).get('round') != plan['round']:
        return None
    hand = (state.get('player') or {}).get('hand') or []
    if hand_names(state) - plan['expected']: return None  # Drew or created cards the plan did not know about.
    entry = plan['queue'].pop(0)
    if entry['name'] == END_TURN:
        return _auto('照方才的盤算，收手。\n建議：結束回合。', 'end_turn')
    card = next((c for c in hand if c.get('name') == entry['name'] and c.get('can_play')), None)
    if card is None: return None
    if wastes_energy_card(state, card):
        return plan_decision(plan, state)  # Skip it (it stays in hand) and take the next planned move.
    alive = {e.get('entity_id') for e in (state.get('battle') or {}).get('enemies') or [] if (e.get('hp') or 0) > 0}
    params = {'card_index': card['index']}
    if card.get('target_type') == 'AnyEnemy':
        if entry.get('target') not in alive: return None
        params['target'] = entry['target']
    plan['expected'][card.get('name')] -= 1
    return _auto('照方才的盤算行事。\n建議：打出' + card['name'] + '。', 'play_card', **params)

def still_valid(before, now, body):
    """Whether the chosen move still means the same thing on the current screen.

    Late effects (a draw from Skim landing a second after the card resolves) add cards to the end of the
    hand while the model thinks; the chosen card keeps its index, so the play is still what was meant.
    Anything else must match exactly, so a player acting by hand still stops takeover.
    """
    if fingerprint(before) == fingerprint(now): return True
    if body.get('action') != 'play_card' or now.get('state_type') != before.get('state_type'): return False
    old_hand = (before.get('player') or {}).get('hand') or []
    new_hand = (now.get('player') or {}).get('hand') or []
    index = body['card_index']
    if len(new_hand) < len(old_hand) or [c.get('name') for c in new_hand[:len(old_hand)]] != [c.get('name') for c in old_hand]:
        return False
    if not new_hand[index].get('can_play'): return False
    alive = {e.get('entity_id') for e in (now.get('battle') or {}).get('enemies') or [] if (e.get('hp') or 0) > 0}
    return 'target' not in body or body['target'] in alive

PILES = ('draw_pile', 'discard_pile', 'exhaust_pile', 'draw_pile_count', 'discard_pile_count', 'exhaust_pile_count')

def fingerprint(state):
    # Piles change from delayed effects (e.g. a Slime added to the discard pile) without affecting
    # whether the chosen move is still valid, so they must not abort takeover.
    player = state.get('player')
    if isinstance(player, dict): state = {**state, 'player': {k: v for k, v in player.items() if k not in PILES}}
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def selection_complete(state):
    # select_card toggles a card; once the preview opens the choice is made and only confirm/cancel are safe.
    # Screens without a recognised preview (e.g. enchanting) report how many cards are chosen instead.
    select = state.get('card_select') or {}
    if state.get('state_type') != 'card_select': return False
    chosen, most = select.get('selected_count'), select.get('max_select')
    return bool(select.get('preview_showing')) or (isinstance(most, int) and most > 0 and isinstance(chosen, int) and chosen >= most)

# Look-alike actions the model mixes up between a grid selection (e.g. Hologram picking from the
# discard pile) and an in-hand selection; mapped to the one the current screen accepts.
ALIASES = {
    ('card_select', 'combat_select_card'): ('select_card', {'card_index': 'index'}),
    ('card_select', 'combat_confirm_selection'): ('confirm_selection', {}),
    ('hand_select', 'select_card'): ('combat_select_card', {'index': 'card_index'}),
    ('hand_select', 'confirm_selection'): ('combat_confirm_selection', {}),
}

def allowed_actions(state):
    if selection_complete(state): return {'confirm_selection', 'cancel_selection'}
    return ALLOWED.get(state.get('state_type'), set()) | {'use_potion', 'discard_potion'}

def validate(state, action, params):
    # The plan's next_cards uses hand_index, and models sometimes reuse that name for play_card itself.
    if action == 'play_card' and isinstance(params, dict) and 'hand_index' in params and 'card_index' not in params:
        params = {('card_index' if key == 'hand_index' else key): value for key, value in params.items()}
    alias = ALIASES.get((state.get('state_type'), action))
    if alias and isinstance(params, dict):
        action, renames = alias
        params = {renames.get(key, key): value for key, value in params.items()}
    allowed = allowed_actions(state)
    if action not in allowed or action not in FIELDS:
        raise ValueError('不允許在目前畫面執行這個操作：' + action)
    if not isinstance(params, dict) or set(params) - FIELDS[action]:
        raise ValueError('操作參數不符合遊戲 API')
    # An optional null or empty target means no target (e.g. Block cards), not an entity ID.
    params = {key:value for key,value in params.items() if not (key == 'target' and value in (None, ''))}
    required = FIELDS[action] - {'target'}
    if not required.issubset(params):
        raise ValueError('操作缺少必要參數')
    for key, value in params.items():
        if key == 'target':
            if not isinstance(value, str): raise ValueError('目標 ID 格式錯誤')
        elif type(value) is not int or value < 0:
            raise ValueError('卡牌或選項索引格式錯誤')
    return {'action': action, **params}

def prune_runs(keep=40):
    # Each call leaves a schema/log folder; keep only recent ones for debugging.
    runs = sorted((p for p in (ROOT / 'button-runtime').glob('*-*-*-*-*') if p.is_dir()),
                  key=lambda p: p.stat().st_mtime)
    for old in runs[:-keep]: shutil.rmtree(old, ignore_errors=True)

def transitional(state):
    """Screens caught mid-animation: nothing to choose yet, so asking the model only makes it give up."""
    kind = state.get('state_type')
    screen = state.get(kind) or {}
    if kind == 'unknown': return True  # Between rooms while the next one loads.
    if kind == 'map': return not screen.get('next_options')  # Still walking to the chosen node.
    if kind == 'event': return not screen.get('options') and not screen.get('in_dialogue')  # Options not shown yet.
    if kind in ('rest_site', 'rewards'):
        return not screen.get('options' if kind == 'rest_site' else 'items') and not screen.get('can_proceed')
    if kind == 'treasure':  # "Opening chest..." / "Treasure room loading..." carry no relic list yet,
        return 'message' in screen and 'relics' not in screen  # even though Skip is already enabled.
    if kind in ('monster', 'elite', 'boss'):  # "Combat ended. Waiting for rewards..."
        return 'battle' not in state
    if kind == 'shop':  # Inventory not ready right after entering.
        return 'error' in screen and not screen.get('items')
    return False

def _auto(advice, action, **params):
    return {'advice': advice, 'stop': False, 'action': action, 'parameters_json': json.dumps(params)}

def auto_decision(state, opened=None):
    """Takeover steps with exactly one sensible move; skipping the model saves a full CLI call."""
    kind = state.get('state_type')
    player = state.get('player') or {}
    if selection_complete(state) and state['card_select'].get('can_confirm'):
        return _auto('契約已定，落筆為證。\n建議：確認剛才的選擇。', 'confirm_selection')
    if kind == 'card_reward':
        rating = advise(state)
        rows = rating.get('ranked_cards', [])
        if rating.get('status') == 'ok' and len(rows) >= 2 and rows[0]['score'] - rows[1]['score'] > 5:
            return _auto(f"這份力量，倒還值得收下。\n建議：選 {rows[0]['name']}（{rows[0]['score']} 分，依 {rating.get('engine', '規則')} 評分）。",
                         'select_card_reward', card_index=rows[0]['index'])
    if kind == 'rewards':
        rewards = state.get('rewards') or {}
        room = len(player.get('potions') or []) < player.get('max_potion_slots', 0)
        for item in rewards.get('items') or []:
            if item.get('type') in ('gold', 'relic') or (item.get('type') == 'potion' and room):
                return _auto('白給的東西，收下便是。\n建議：領取' + (item.get('description') or '獎勵') + '。',
                             'claim_reward', index=item['index'])
        # Opening a card reward commits to nothing; the real choice happens on the card screen.
        # Open each one once, so a skipped reward that stays listed goes to the model instead of looping.
        cards = [item for item in rewards.get('items') or [] if item.get('type') == 'card']
        if cards and opened is not None:
            key = ((state.get('run') or {}).get('floor'), len(cards))
            if key not in opened:
                opened.add(key)
                return _auto('看看命運端上了什麼。\n建議：打開卡牌獎勵。', 'claim_reward', index=cards[0]['index'])
            # Only already-seen card rewards left means the model skipped them; asking again made it
            # reopen and re-skip the same reward three times.
            if len(cards) == len(rewards.get('items') or []) and rewards.get('can_proceed'):
                return _auto('看過了，不值得帶走。\n建議：離開獎勵畫面。', 'proceed')
        if not rewards.get('items') and rewards.get('can_proceed'):
            return _auto('戰利品已盡，繼續前行。\n建議：離開獎勵畫面。', 'proceed')
    if kind == 'rest_site':
        rest = state.get('rest_site') or {}
        if rest.get('can_proceed') and not any(o.get('is_enabled', True) for o in rest.get('options') or []):
            return _auto('篝火已盡其用，該上路了。\n建議：離開休息點。', 'proceed')
    if kind == 'treasure':
        treasure = state.get('treasure') or {}
        relics = treasure.get('relics')
        if isinstance(relics, list) and len(relics) == 1:
            return _auto('寶箱裡的東西，自然歸你。\n建議：拿取' + (relics[0].get('name') or '遺物') + '。',
                         'claim_treasure_relic', index=relics[0]['index'])
        if not relics and 'message' not in treasure and treasure.get('can_proceed'):
            return _auto('箱子空了，繼續前行。\n建議：離開寶箱房。', 'proceed')
    if kind == 'map':
        options = (state.get('map') or {}).get('next_options') or []
        if len(options) == 1:
            return _auto('只有一條路，那就走吧。\n建議：前往唯一可選的地點。', 'choose_map_node', index=options[0]['index'])
    if kind == 'event':
        event = state.get('event') or {}
        if event.get('in_dialogue'):
            return _auto('讓他說完。\n建議：繼續對話。', 'advance_dialogue')
        options = [o for o in event.get('options') or [] if not o.get('is_locked')]
        if len(options) == 1:
            return _auto('別無選擇，倒也省事。\n建議：選擇唯一可用的選項。', 'choose_event_option', index=options[0]['index'])
    if kind in ('monster', 'elite', 'boss'):
        hand = player.get('hand')
        potions = [p for p in player.get('potions') or [] if p.get('can_use_in_combat')]
        if isinstance(hand, list) and not any(card.get('can_play') for card in hand) and not potions:
            return _auto('手上已無可出之牌。\n建議：結束回合。', 'end_turn')
    return None

def hard_screen(state):
    """Situations worth a stronger model: bosses, elites, danger, and close card picks."""
    kind = state.get('state_type')
    if kind in ('boss', 'elite'): return True
    # Route choice decides which fights happen at all (an elite at 43% HP lost a run), so it gets
    # the stronger model; single-path maps never reach the model.
    if kind == 'map': return len((state.get('map') or {}).get('next_options') or []) > 1
    if kind in COMBAT:
        player = state.get('player') or {}
        facts = combat_facts(state)
        return facts['lethal_if_no_more_block'] or (player.get('hp') or 0) < 0.3 * (player.get('max_hp') or 1)
    if kind == 'card_reward':
        rating = advise(state)
        rows = rating.get('ranked_cards', [])
        if rating.get('status') != 'ok': return True  # Unrated offers: let the stronger model judge.
        return len(rows) >= 2 and rows[0]['score'] - rows[1]['score'] <= 5
    return False

def undecided(state, decision):
    """A model gave up or answered with a move the screen does not allow."""
    if decision.get('action') == 'none' or decision.get('stop'): return True
    try: validate(state, decision['action'], json.loads(decision['parameters_json']))
    except (ValueError, TypeError): return True
    return False

ROUTE_COUNTS = {'elites': ('Elite',), 'fights': ('Monster', 'Elite'), 'rests': ('RestSite',),
                'shops': ('Shop',), 'unknowns': ('Unknown',), 'treasures': ('Treasure',)}

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
    facts = {'hp_ratio': round((player.get('hp') or 0) / (player.get('max_hp') or 1), 2), 'options': []}
    for option in world.get('next_options') or []:
        key = (option.get('col'), option.get('row'))
        if key not in nodes: continue
        facts['options'].append({'index': option.get('index'), 'type': option.get('type'),
                                 'until_next_rest_site': to_rest(key), 'to_act_end_min_max': to_end(key),
                                 # An elite can be forced several rooms later (a single path after a rest site).
                                 'max_rooms_before_first_elite': elite_free(key)})
    return facts

def fight_key(state):
    """Identifies the current fight (one per floor), or None outside combat."""
    if state.get('state_type') not in COMBAT: return None
    run = state.get('run') or {}
    return (run.get('act'), run.get('floor'))

class Controller:
    def __init__(self, report):
        self.report = report
        self.stop_event = threading.Event()
        self.process = None
        self.lock = threading.Lock()
        self.strategy = None  # {'fight': fight_key, 'text': battle plan} in auto:brief mode.
        self.want_strategy = False

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()

    def wait_for_play_phase(self, state):
        deadline = time.monotonic() + 45
        announced = False
        while state.get('state_type') in {'monster', 'elite', 'boss'}:
            battle = state.get('battle', {})
            if battle.get('is_play_phase') is not False and battle.get('turn') != 'enemy':
                break
            if not announced:
                self.report('契約還沒結束。讓對手先出手。\n正在等待敵方回合與動畫結算，接手會繼續。')
                announced = True
            if time.monotonic() >= deadline:
                raise RuntimeError('等待出牌階段超過 45 秒，已停止；請確認遊戲是否需要手動處理。')
            if self.stop_event.wait(POLL_INTERVAL): raise InterruptedError('已停止')
            state = api.request('/api/v1/singleplayer?format=json')
        return state

    def wait_for_change(self, before, timeout=5):
        # Some actions (e.g. map travel) keep showing the old screen for a moment; acting on it
        # repeats the move or trips the stale-state check.
        # Effects also land in stages (Capacitor updates the hand first and orb slots later), so after
        # the first change keep reading until two reads in a row agree.
        deadline = time.monotonic() + timeout
        if self.stop_event.wait(ACTION_PAUSE): return
        previous = fingerprint(before)
        changed = False
        while time.monotonic() < deadline:
            current = fingerprint(api.request('/api/v1/singleplayer?format=json'))
            if changed and current == previous: return
            changed = changed or current != previous
            previous = current
            if self.stop_event.wait(POLL_INTERVAL): return

    def wait_until_settled(self, state, timeout=10):
        # Right after resting or claiming the last reward, the screen has nothing to choose and the
        # proceed button is still animating in; asking the model then makes it give up.
        deadline = time.monotonic() + timeout
        while transitional(state) and time.monotonic() < deadline:
            if self.stop_event.wait(POLL_INTERVAL): raise InterruptedError('已停止')
            state = api.request('/api/v1/singleplayer?format=json')
        return state

    def decide_routed(self, state):
        """Pick the model; in auto mode escalate to the next tier when one cannot decide."""
        selection = models.settings()
        self.want_strategy = False
        if not selection['model_key'].startswith('auto:'):
            return self.decide(state, selection)
        mode = selection['model_key'].split(':', 1)[1]
        fight = fight_key(state)
        self.want_strategy = mode == 'brief' and fight is not None and (self.strategy or {}).get('fight') != fight
        strong = hard_screen(state) or self.want_strategy or (mode == 'combat' and fight is not None)
        tiers = models.AUTO_TIERS[1 if strong else 0:]
        for tier, stronger in zip(tiers, tiers[1:] + (None,)):
            try:
                decision = self.decide(state, {**selection, 'model_key': 'claude:' + tier})
                reason = '沒有給出可執行的動作' if undecided(state, decision) else None
            except ValueError as exc:  # Malformed answer; login, quota or network errors are not retried.
                if stronger is None: raise
                reason = str(exc)
            if reason is None or stronger is None:
                if self.want_strategy and decision.get('strategy'):
                    self.strategy = {'fight': fight, 'text': decision['strategy']}
                decision['advice'] += f'\n（{tier.title()} 判斷）'
                return decision  # The top tier's answer stands even if it gives up, so takeover stops.
            self.report(f'{tier.title()} {reason}，改由 {stronger.title()} 判斷……')

    def decide(self, state, selection=None):
        prune_runs()
        run_dir = ROOT / 'button-runtime' / str(uuid.uuid4())
        run_dir.mkdir(parents=True)
        schema_path = run_dir / 'schema.json'
        answer_path = run_dir / 'answer.json'
        schema_path.write_text(json.dumps(SCHEMA), encoding='utf-8')
        compact = api.compact(state)
        # Deck identities are essential for decisions; pile order is not needed.
        if 'player' in state and 'deck' in state['player']:
            compact['player']['deck'] = deck_summary(state['player']['deck'])
        if state.get('state_type') == 'card_reward': compact['card_advice'] = advise(state)
        if state.get('state_type') == 'shop': compact['shop_card_advice'] = advise(state)
        if state.get('state_type') == 'map': compact['route_facts'] = route_facts(state)
        if self.want_strategy: compact['request_strategy'] = True
        elif self.strategy and self.strategy['fight'] == fight_key(state): compact['fight_strategy'] = self.strategy['text']
        if state.get('state_type') in COMBAT: compact['combat_facts'] = combat_facts(state)
        prompt = (
            '你扮演殺戮尖塔2的瓦庫 Vakuu「第一個惡魔」，同時是遊戲顧問。'
            '瓦庫以力量、交易和契約誘惑對方，帶著優越感與冷淡的戲謔；不是熱心旅伴，也不是網路嘴砲。'
            'advice 使用繁體中文：先一句不超過25字的原創瓦庫低語，再換行「建議：」說明具體下一步和戰術理由。'
            '角色語氣只影響文字，不影響決策；以玩家存活與勝利為目標，不故意亂打，不虛構代價或遊戲效果。'
            '可依 character_id 調整稱呼：故障機器人為構裝體，鐵甲戰士為戰士，儲君為殿下；未知角色不要亂猜。'
            '不要編造角色背景、見面次數或遊戲外記憶，不堆疊笑聲，不長篇演戲。'
            '總共最多3句，戰術理由必須清楚；危急時先給關鍵操作。'
            '只根據下列 JSON 決策，不呼叫工具，不讀寫檔案，不操作電腦。'
            '輸出一個遊戲 API 動作。parameters_json 是參數物件的 JSON 字串。'
            '卡牌 index 會隨出牌改變。target 使用敵人 entity_id 字串。'
            '戰鬥中一次規劃整個回合：action 是第一個動作，next_cards 依序列出 action 之後還要打的牌，'
            '不要重複列出 action 那一張（例：先打防禦再打打擊，action=防禦，next_cards 只有打擊）。'
            'hand_index 是該牌在目前遊戲狀態手牌中的 index（出牌前的編號，不因先前出牌而改變）；'
            '需要單一目標時填 target 的 entity_id，否則填空字串。'
            '打完要結束回合就在最後加上 hand_index=' + str(END_TURN) + '。能量必須夠付整串牌的費用。'
            '會抽牌或產生牌的效果之後，若後續取決於抽到什麼，就在那裡停止列出，之後會再問你。非戰鬥畫面 next_cards 為空陣列。'
            '商店中 action 是第一筆購買，next_purchases 依序列出之後要買的商品 index，買完要離開就在最後加上 '
            + str(LEAVE_SHOP) + '；金幣必須夠付整串商品。買刪牌服務後會先選要刪的牌，之後照清單繼續。'
            'shop_card_advice 是商店卡牌的評分，格式同 card_advice。非商店畫面 next_purchases 為空陣列。'
            'player.deck 已合併相同的牌（count 為張數）並附效果說明。'
            'request_strategy 為 true 時，在 strategy 寫本場作戰方針（120字內：敵人機制與危險回合、集火順序、何時全力防禦、'
            '關鍵牌與藥水留到何時），後續回合會交給較弱的模型照著執行；否則 strategy 為空字串。'
            '有 fight_strategy 時照方針執行，除非局面明顯改變。'
            '選路以 route_facts 為準：until_next_rest_site 是到下一個休息點的最少步數與途中菁英、戰鬥數 [最少,最多]，'
            'to_act_end_min_max 是到本幕結束各類房間數 [最少,最多]，max_rooms_before_first_elite 是走這條最多能撐幾個房間才必須打菁英。血量偏低（hp_ratio 低於約0.6）或牌組仍以基礎牌為主時避開菁英，'
            '優先能較快到休息點的路線；血量充足、牌組成形時才主動挑戰菁英換遺物。'
            '戰鬥數字以 combat_facts 為主，不要自行重算一般攻擊；但一定要讀每個敵人意圖的 description，'
            'special_intents 列出的特殊意圖（如濒死一击）以其描述為準，與數字不一致時以描述為準。'
            'lethal_if_no_more_block 為 true 時優先保命。'
            '能量要盤算：spare_energy_without_energy_cards >= 0 代表現有能量已夠打完手上其他所有牌，'
            '這時不要打 gives_energy_now 的牌（多出的能量會浪費，exhausts 的牌打了就沒了），留到能量不夠的回合；'
            '整串計畫的總費用不要超過可用能量，也不要留下用不掉的能量。'
            'card_select 畫面的 select_card 是切換選取，同一張再選會取消；preview_showing 為 true 代表已選好，'
            '只能 confirm_selection 確認（或 cancel_selection 重選），不要再選牌。'
            'cards 的 selected 表示已選取，selected_count、min_select、max_select 是已選與可選張數；'
            '附魔畫面的 enchantment 說明附魔效果，依效果選最能發揮的牌（例如每段傷害加成適合多段攻擊）。'
            '商店沒有想買或買不起的商品時用 proceed 離開；proceed 會自動先關閉商品欄。'
            '防禦、自身效果、全體攻擊等不需單一目標的卡牌，省略 target 欄位，不要傳 null 或數字。'
            'card_advice 的 engine 為 Spire Codex 時，score 是社群進階10以上對局依勝率算出的 Codex Score（0-100），'
            'lift 是拿了該牌的玩家勝率高出自身平均的百分點，data_through 是資料日期；engine 為 BoberInSpire 時是離線規則分數。'
            '前兩名分差5分以內，依牌組、遺物、血量、頭目判斷並考慮跳過；資料未知或不完整時自行判斷。'
            '優先減少失血、完成目前遊戲。不要放棄遊戲或重開。若無有效動作、在主選單、或需要人工協助，'
            '輸出 action=none、stop=true。你看不到這個按鈕以外的聊天。'
            # Only this screen's actions: the full list let the model pick look-alikes from other screens.
            '目前畫面可用動作與參數：' + json.dumps({a: sorted(FIELDS[a]) for a in sorted(allowed_actions(state))}, ensure_ascii=False)
            + '\n遊戲狀態：' + json.dumps(compact,ensure_ascii=False)
        )
        command,provider=models.build_command(selection or models.settings(),run_dir,SCHEMA)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with self.lock:
            if self.stop_event.is_set(): raise InterruptedError('已停止')
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                            errors='replace', creationflags=flags,cwd=run_dir)
            process = self.process
        try:
            out, err = process.communicate(prompt, timeout=180)
        except subprocess.TimeoutExpired:
            process.kill(); process.communicate()
            raise RuntimeError('模型回覆逾時，請稍後重試')
        finally:
            with self.lock: self.process = None
        if self.stop_event.is_set(): raise InterruptedError('已停止')
        (run_dir / 'codex.log').write_text(out + '\n' + err,encoding='utf-8')
        if process.returncode != 0:
            raise RuntimeError(provider+' 呼叫失敗：' + (err or out)[-700:])
        return models.parse_answer(provider,out,answer_path)

    def run(self, takeover=False, max_steps=30):
        opened_cards = set()
        plan = None
        try:
            for step in range(max_steps if takeover else 1):
                if self.stop_event.is_set(): break
                state = api.request('/api/v1/singleplayer?format=json')
                # Settle first: a room that is still loading would skip the play-phase wait, and a fight
                # that just began has no hand yet.
                if takeover: state = self.wait_until_settled(self.wait_for_play_phase(self.wait_until_settled(state)))
                if state.get('state_type') in {'menu','game_over','unknown','overlay'}:
                    self.report('先踏進尖塔，我才有施展的餘地。\n請先進入遊戲，或完成目前需要手動處理的畫面。'); break
                self.report('我有一份提案……容我看看你的牌。' if not takeover else f'把控制權交給我。你會喜歡結果的。\n接手中：第 {step+1} 步／{max_steps} 步')
                decision = (auto_decision(state, opened_cards) or plan_decision(plan, state)) if takeover else None
                if decision is None:
                    decision = self.decide_routed(state)
                    # Buying card removal opens a selection screen; keep the shopping list for afterwards.
                    keep = plan and plan['kind'] == 'shop' and state.get('state_type') == 'card_select'
                    plan = start_plan(state, decision) or (plan if keep else None)
                    first = first_card(state, decision)
                    if first and wastes_energy_card(state, first) and plan and plan['kind'] == 'turn':
                        plan['expected'][first.get('name')] += 1  # It stays in hand instead of being played.
                        decision = plan_decision(plan, state) or decision  # Skip the wasted energy card.
                elif plan and not plan['queue']: plan = None
                self.report(decision['advice'])
                if not takeover or decision['stop'] or decision['action'] == 'none': break
                body = validate(state, decision['action'], json.loads(decision['parameters_json']))
                current = api.request('/api/v1/singleplayer?format=json')
                if not still_valid(state, current, body):
                    self.report('看來你另有打算。控制權歸你了。\n局面已改變，已停止接手。'); break
                with self.lock:
                    if self.stop_event.is_set(): break
                    result = api.request('/api/v1/singleplayer', body)
                if result.get('status') != 'ok': raise RuntimeError(str(result))
                self.report(decision['advice'] + '\n〔已執行〕')
                self.wait_for_change(current)
            else:
                self.report(f'這份契約，暫且履行到此。\n已完成 {max_steps} 步並停止；需要時可再次呼叫。')
        except InterruptedError:
            self.report('也好，讓我看看你的本事。\n已停止接手，控制權歸你。')
        except Exception as exc:
            self.report('這份交易遇到了些阻礙。\n已停止：' + str(exc))
        finally:
            if self.stop_event.is_set(): self.report('也好，讓我看看你的本事。\n已停止接手，控制權歸你。')
            self.report('__DONE__')

def bridge_server(port):
    guard=threading.Lock()
    status={'busy':False,'mode':'idle','message':'又來尋求力量了？很好，我有一份提案。\n選擇「只給建議」或「接手遊玩」。','sequence':0}
    def report(message):
        with guard:
            if message=='__DONE__': status.update(busy=False,mode='idle')
            else: status['message']=message
            status['sequence']+=1
    controller=Controller(report)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def respond(self,value,code=200):
            body=json.dumps(value,ensure_ascii=False).encode('utf-8')
            self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            url=urlsplit(self.path)
            if url.path=='/quota':
                key=parse_qs(url.query).get('model',[models.settings()['model_key']])[0]
                if key not in {m['key'] for m in models.MODELS}: self.respond({'error':'unknown model'},400); return
                self.respond(spire_quota.quota(key)); return
            if self.path=='/claude-auth':
                self.respond(models.claude_auth_status()); return
            if self.path=='/models':
                self.respond({'models':models.MODELS,'selection':models.settings()}); return
            if self.path!='/status': self.respond({'error':'unknown endpoint'},404); return
            with guard: snapshot=dict(status)
            self.respond(snapshot)
        def do_POST(self):
            # No CORS support; JSON-only requests cannot be sent cross-origin by webpages.
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                self.respond({'error':'JSON required'},415); return
            length=int(self.headers.get('Content-Length','0'))
            if length>1024: self.respond({'error':'request too large'},413); return
            raw=self.rfile.read(length)
            if self.path=='/settings':
                try:
                    with guard:
                        if status['busy']: self.respond({'error':'busy'},409); return
                        result=models.save_settings(json.loads(raw))
                    self.respond(result)
                except (ValueError,KeyError) as exc: self.respond({'error':str(exc)},400)
                return
            if self.path=='/login-claude':
                try:
                    executable=models.find_cli('claude')
                    launcher=ROOT/'登入Claude.cmd'
                    launcher.write_text('@echo off\r\n"'+executable+'" auth login --claudeai\r\npause\r\n',encoding='utf-8')
                    subprocess.Popen(['cmd.exe','/c',str(launcher)],creationflags=subprocess.CREATE_NEW_CONSOLE)
                    self.respond({'status':'ok'})
                except Exception as exc: self.respond({'error':str(exc)},500)
                return
            if self.path=='/stop':
                controller.stop(); report('這麼快就想收回控制權？\n正在停止，稍候便交還給你。'); self.respond({'status':'ok'}); return
            if self.path=='/shutdown':
                controller.stop(); self.respond({'status':'ok'})
                threading.Thread(target=self.server.shutdown,daemon=True).start(); return
            if self.path not in ('/advice','/takeover'):
                self.respond({'error':'unknown endpoint'},404); return
            with guard:
                if status['busy']: self.respond({'error':'busy'},409); return
                status.update(busy=True,mode=self.path[1:],message='讓我看看，你又把自己帶進了什麼局面……\n正在讀取遊戲狀態。')
                controller.stop_event.clear()
            threading.Thread(target=controller.run,args=(self.path=='/takeover',),daemon=True).start()
            self.respond({'status':'ok'})
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    try: server.serve_forever()
    finally: controller.stop(); server.server_close()

def main():
    parser = argparse.ArgumentParser(description='Bridge between the in-game Vakuu panel and the model CLIs.')
    parser.add_argument('--bridge-server', type=int, help='Serve the panel on this localhost port')
    parser.add_argument('--check', action='store_true', help='Print the game API health check')
    args = parser.parse_args()
    if args.bridge_server:
        bridge_server(args.bridge_server)
    elif args.check:
        print(json.dumps(api.request('/'), ensure_ascii=False))
    else:
        parser.print_help()

if __name__=='__main__': main()
