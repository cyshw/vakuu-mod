"""Game action rules: what each screen allows, validating model moves, and comparing states."""
import hashlib
import json
import re

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
            # potion_slot >= 0 plans drinking that potion instead of a card (hand_index ignored).
            'properties': {'hand_index': {'type': 'integer'}, 'target': {'type': 'string'}, 'potion_slot': {'type': 'integer'}},
            'required': ['hand_index', 'target', 'potion_slot']}},
        # Shop items to buy after `action`, so a shop visit is planned in one call.
        'next_purchases': {'type': 'array', 'items': {'type': 'integer'}},
        # A per-fight battle plan, written only when the prompt asks for one (auto:brief).
        'strategy': {'type': 'string'},
        # The rest of the route after the chosen map node, walked without asking again.
        'next_nodes': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'col': {'type': 'integer'}, 'row': {'type': 'integer'}},
            'required': ['col', 'row']}},
    }, 'required': ['advice', 'stop', 'action', 'parameters_json', 'next_cards', 'next_purchases', 'strategy',
                    'next_nodes']
}
COMBAT = {'monster', 'elite', 'boss'}
END_TURN = -1  # hand_index that means "end the turn" in next_cards.
LEAVE_SHOP = -1  # Item index that means "leave the shop" in next_purchases.

def still_valid(before, now, body):
    """Whether the chosen move still means the same thing on the current screen.

    Late effects (a draw from Skim landing a second after the card resolves) add cards to the end of the
    hand while the model thinks; the chosen card keeps its index (and a potion its slot), so the move is
    still what was meant.
    Anything else must match exactly, so a player acting by hand still stops takeover.
    """
    if fingerprint(before) == fingerprint(now): return True
    if body.get('action') == 'confirm_selection' and before.get('state_type') == now.get('state_type') == 'card_select':
        old = before.get('card_select') or {}
        new = now.get('card_select') or {}
        # Transform previews can reroll between API reads. Confirming applies
        # to the selected original card; it does not choose the preview result.
        if (old.get('screen_type') == new.get('screen_type') == 'transform'
                and old.get('can_confirm') and new.get('can_confirm')
                and selection_complete(before) and selection_complete(now)):
            without_preview = lambda s: {**s, 'card_select': {
                k: v for k, v in s['card_select'].items() if k != 'preview_cards'}}
            return fingerprint(without_preview(before)) == fingerprint(without_preview(now))
    if body.get('action') not in ('play_card', 'use_potion') or now.get('state_type') != before.get('state_type'): return False
    old_hand = (before.get('player') or {}).get('hand') or []
    new_hand = (now.get('player') or {}).get('hand') or []
    if len(new_hand) < len(old_hand) or [c.get('name') for c in new_hand[:len(old_hand)]] != [c.get('name') for c in old_hand]:
        return False
    if body['action'] == 'use_potion':
        # Run 28: Cool Head's draw landed while the model chose a Block Potion, which stopped takeover.
        potions = lambda state: [(p.get('slot'), p.get('name')) for p in (state.get('player') or {}).get('potions') or []]
        if potions(before) != potions(now): return False
    elif not new_hand[body['card_index']].get('can_play'): return False
    alive = {e.get('entity_id') for e in (now.get('battle') or {}).get('enemies') or [] if (e.get('hp') or 0) > 0}
    return 'target' not in body or body['target'] in alive

PILES = ('draw_pile', 'discard_pile', 'exhaust_pile', 'draw_pile_count', 'discard_pile_count', 'exhaust_pile_count')

def fingerprint(state):
    # Piles change from delayed effects (e.g. a Slime added to the discard pile) without affecting
    # whether the chosen move is still valid, so they must not abort takeover.
    # Execution telemetry changes independently of the decision snapshot.
    state = {k: v for k, v in state.items() if k != 'execution'}
    player = state.get('player')
    if isinstance(player, dict): state = {**state, 'player': {k: v for k, v in player.items() if k not in PILES}}
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

HAND_PICK_COUNT = re.compile(r'[选選]择\s*(\d+)\s*[张張]')

def hand_selection_complete(state):
    """In-hand picks (e.g. "选择1张牌来消耗") report no count; read it from the prompt."""
    if state.get('state_type') != 'hand_select': return False
    select = state.get('hand_select') or {}
    wanted = HAND_PICK_COUNT.search(str(select.get('prompt') or ''))
    return bool(wanted) and len(select.get('selected_cards') or []) >= int(wanted.group(1)) and bool(select.get('can_confirm'))

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
    # Haiku answered "play" on Exhaust-a-card prompts; a hand pick takes no target (None drops a key).
    ('hand_select', 'play_card'): ('combat_select_card', {'target': None}),
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
        params = {renames.get(key, key): value for key, value in params.items() if renames.get(key, key) is not None}
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
    if action == 'claim_reward' and potion_belt_full(state):
        # The game ignores the claim; Sonnet asked for the same Fruit Juice five times in a row.
        item = next((i for i in (state.get('rewards') or {}).get('items') or [] if i.get('index') == params['index']), {})
        if item.get('type') == 'potion': raise ValueError('藥水欄已滿，先用 discard_potion 丟掉一瓶才能領取，或 proceed 離開')
    return {'action': action, **params}

def potion_belt_full(state):
    player = state.get('player') or {}
    slots = player.get('max_potion_slots')
    return isinstance(slots, int) and len(player.get('potions') or []) >= slots

def waiting_for_teammates(state):
    """Co-op: our end turn or vote is submitted and the others have not finished theirs."""
    if state.get('game_mode') != 'multiplayer': return False
    kind = state.get('state_type')
    if kind in COMBAT:
        me = next((p for p in state.get('players') or [] if p.get('is_local')), {})
        return bool(me.get('is_ready_to_end_turn')) and not (state.get('battle') or {}).get('all_players_ready')
    screen = state.get(kind) or {}
    for votes, done in (('votes', 'all_voted'), ('bids', 'all_bid')):
        mine = next((v for v in screen.get(votes) or [] if v.get('is_local')), None)
        if mine and mine.get('voted') and not screen.get(done): return True
    return False

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
    if kind == 'card_select':  # An enchant preview with confirm still disabled is applying itself;
        return screen.get('preview_showing') is True and screen.get('can_confirm') is False  # confirming failed.
    return False
