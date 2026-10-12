"""Moves takeover makes without the model: forced moves, and the rest of a planned turn or shopping list."""
import json
from collections import Counter
from combat.facts import wastes_energy_card
from combat.relics import after_card_relics
from combat.triggers import after_card_triggers
from core.rules import COMBAT, END_TURN, LEAVE_SHOP, hand_selection_complete, program_move, selection_complete
from deck.advisor import advise


def hand_names(state):
    return Counter(c.get('name') for c in (state.get('player') or {}).get('hand') or [])

def card_signature(card):
    # Index and playability change as energy/hand size changes. Other fields,
    # including cost and resolved description, must stay exactly as expected.
    return json.dumps({k: v for k, v in card.items() if k not in
                       {'index', 'can_play', 'unplayable_reason'}}, sort_keys=True, ensure_ascii=False)

def hand_signatures(state):
    return Counter(card_signature(c) for c in (state.get('player') or {}).get('hand') or [])

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
        return program_move('該買的都買了，走吧。\n建議：離開商店。', 'proceed')
    item = next((i for i in (state.get('shop') or {}).get('items') or [] if i.get('index') == index), None)
    if not item or not item.get('is_stocked') or not item.get('can_afford'): return None
    name = item.get('card_name') or item.get('relic_name') or item.get('potion_name') or '刪牌服務'
    return program_move('照方才的盤算，買下它。\n建議：購買' + name + '。', 'shop_purchase', index=index)

def start_turn_plan(state, decision):
    """Remember the model's remaining moves for this turn, with the hand we expect afterwards."""
    if state.get('state_type') not in COMBAT or decision['action'] not in ('play_card', 'end_turn', 'use_potion') \
            or not decision.get('next_cards'):
        return None
    # The model refers to cards by their index in the hand it was shown; store the game's exact names,
    # since the model rewrites names (e.g. simplified to traditional Chinese) and indices shift as cards are played.
    hand = (state.get('player') or {}).get('hand') or []
    potions = {p.get('slot'): p for p in (state.get('player') or {}).get('potions') or []}
    queue = []
    combat_ids = {e.get('entity_id'): e.get('combat_id') for e in (state.get('battle') or {}).get('enemies') or []}
    def target_identity(target):
        cid = combat_ids.get(target)
        return {'target_combat_id': cid} if type(cid) is int else {}
    for entry in decision.get('next_cards') or []:
        slot = entry.get('potion_slot')
        if isinstance(slot, int) and slot >= 0:
            if slot not in potions: break
            queue.append({'name': potions[slot].get('name'), 'potion': slot, 'target': entry.get('target') or '',
                          **target_identity(entry.get('target'))})
            continue
        index = entry.get('hand_index')
        if index == END_TURN: queue.append({'name': END_TURN, 'target': ''}); break
        if not isinstance(index, int) or not 0 <= index < len(hand): break
        queue.append({'name': hand[index].get('name'), 'target': entry.get('target') or '',
                      **target_identity(entry.get('target')),
                      **({'signature': card_signature(hand[index])} if decision.get('candidate_id') else {})})
    if not queue: return None
    expected = hand_names(state)
    if decision['action'] == 'play_card':
        index = json.loads(decision['parameters_json']).get('card_index')
        if not isinstance(index, int) or not 0 <= index < len(hand): return None
        expected[hand[index].get('name')] -= 1
    plan = {'kind': 'turn', 'round': state['battle'].get('round'), 'queue': queue, 'expected': expected}
    if decision.get('candidate_id'):
        guarded = hand_signatures(state)
        if decision['action'] == 'play_card': guarded[card_signature(hand[index])] -= 1
        player = state.get('player') or {}
        plan.update(candidate_id=decision['candidate_id'], guarded_hand=guarded,
                    guarded_status=after_card_triggers(player.get('status') or [], hand[index]) if decision['action'] == 'play_card' else player.get('status') or [],
                    guarded_relics=after_card_relics(player.get('relics') or [], hand[index]) if decision['action'] == 'play_card' else player.get('relics') or [],
                    guarded_energy=player.get('energy', 0) - (int(hand[index]['cost']) if decision['action'] == 'play_card' else 0))
    return plan

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
    if plan.get('candidate_id'):
        player = state.get('player') or {}
        if hand_signatures(state) != +plan['guarded_hand'] or player.get('energy') != plan['guarded_energy'] \
                or (player.get('status') or []) != plan['guarded_status'] \
                or (player.get('relics') or []) != plan['guarded_relics']:
            return None
    if hand_names(state) - plan['expected']: return None  # Drew or created cards the plan did not know about.
    entry = plan['queue'].pop(0)
    if 'target_combat_id' in entry:
        target = next((e for e in (state.get('battle') or {}).get('enemies') or []
                       if e.get('combat_id') == entry['target_combat_id'] and (e.get('hp') or 0) > 0), None)
        if target is None: return None
        entry['target'] = target['entity_id']
    if entry['name'] == END_TURN:
        return program_move('照方才的盤算，收手。\n建議：結束回合。', 'end_turn')
    if 'potion' in entry:
        potion = next((p for p in (state.get('player') or {}).get('potions') or []
                       if p.get('slot') == entry['potion'] and p.get('name') == entry['name']), None)
        if potion is None: return None
        params = {'slot': entry['potion']}
        if potion.get('target_type') == 'AnyEnemy':
            alive = {e.get('entity_id') for e in (state.get('battle') or {}).get('enemies') or [] if (e.get('hp') or 0) > 0}
            if entry.get('target') not in alive: return None
            params['target'] = entry['target']
        return program_move('照方才的盤算，喝下它。\n建議：使用' + potion['name'] + '。', 'use_potion', **params)
    card = next((c for c in hand if c.get('name') == entry['name'] and c.get('can_play')
                 and (not entry.get('signature') or card_signature(c) == entry['signature'])), None)
    if card is None: return None
    if wastes_energy_card(state, card):
        return plan_decision(plan, state)  # Skip it (it stays in hand) and take the next planned move.
    alive = {e.get('entity_id') for e in (state.get('battle') or {}).get('enemies') or [] if (e.get('hp') or 0) > 0}
    params = {'card_index': card['index']}
    if card.get('target_type') == 'AnyEnemy':
        if entry.get('target') not in alive: return None
        params['target'] = entry['target']
    plan['expected'][card.get('name')] -= 1
    if plan.get('candidate_id'):
        plan['guarded_hand'][card_signature(card)] -= 1
        plan['guarded_energy'] -= int(card['cost'])
        plan['guarded_relics'] = after_card_relics(plan['guarded_relics'], card)
        plan['guarded_status'] = after_card_triggers(plan['guarded_status'], card)
    return program_move('照方才的盤算行事。\n建議：打出' + card['name'] + '。', 'play_card', **params)

# Auto-pick a card reward only when its Codex Score is at least this; guides warn that taking every
# decent card bloats the deck, so weaker winners go to the model, which may skip.
AUTO_PICK_MIN_SCORE = 60

def auto_decision(state, opened=None):
    """Takeover steps with exactly one sensible move; skipping the model saves a full CLI call."""
    kind = state.get('state_type')
    player = state.get('player') or {}
    if hand_selection_complete(state):
        return program_move('選好了，落筆。\n建議：確認選擇的牌。', 'combat_confirm_selection')
    if selection_complete(state) and state['card_select'].get('can_confirm'):
        return program_move('契約已定，落筆為證。\n建議：確認剛才的選擇。', 'confirm_selection')
    if kind == 'card_reward':
        rating = advise(state)
        rows = rating.get('ranked_cards', [])
        fit = rows[0].get('deck_fit', {}) if rows else {}
        direction = rating.get('deck_direction') or {}
        supported = bool(direction and not direction.get('unknown_description_count') and
                         fit.get('effect_known') and not fit.get('copies_owned') and
                         (fit.get('fills_needs') or fit.get('synergies')))
        # Different gaps are a strategic tradeoff even when population scores
        # differ widely. Keep skip and alternative synergies available to the model.
        if supported:
            for other in rows[1:]:
                alternative = other.get('deck_fit') or {}
                if (not alternative.get('effect_known') or
                        set(alternative.get('fills_needs') or []) - set(fit.get('fills_needs') or []) or
                        set(alternative.get('synergies') or []) - set(fit.get('synergies') or [])):
                    supported = False; break
        # A clear but mediocre winner is not worth a deck slot; let the model weigh skipping it.
        if supported and rating.get('status') == 'ok' and len(rows) >= 2 and rows[0]['score'] - rows[1]['score'] > 5 \
                and rows[0]['score'] >= AUTO_PICK_MIN_SCORE:
            return program_move(f"這份力量，倒還值得收下。\n建議：選 {rows[0]['name']}（{rows[0]['score']} 分，依 {rating.get('engine', '規則')} 評分）。",
                         'select_card_reward', card_index=rows[0]['index'])
    if kind == 'rewards':
        rewards = state.get('rewards') or {}
        room = len(player.get('potions') or []) < player.get('max_potion_slots', 0)
        for item in rewards.get('items') or []:
            if item.get('type') in ('gold', 'relic') or (item.get('type') == 'potion' and room):
                return program_move('白給的東西，收下便是。\n建議：領取' + (item.get('description') or '獎勵') + '。',
                             'claim_reward', index=item['index'])
        # Opening a card reward commits to nothing; the real choice happens on the card screen.
        # Open each one once, so a skipped reward that stays listed goes to the model instead of looping.
        cards = [item for item in rewards.get('items') or [] if item.get('type') == 'card']
        if cards and opened is not None:
            key = ((state.get('run') or {}).get('floor'), len(cards))
            if key not in opened:
                opened.add(key)
                return program_move('看看命運端上了什麼。\n建議：打開卡牌獎勵。', 'claim_reward', index=cards[0]['index'])
            # Only already-seen card rewards left means the model skipped them; asking again made it
            # reopen and re-skip the same reward three times.
            if len(cards) == len(rewards.get('items') or []) and rewards.get('can_proceed'):
                return program_move('看過了，不值得帶走。\n建議：離開獎勵畫面。', 'proceed')
        if not rewards.get('items') and rewards.get('can_proceed'):
            return program_move('戰利品已盡，繼續前行。\n建議：離開獎勵畫面。', 'proceed')
    if kind == 'rest_site':
        rest = state.get('rest_site') or {}
        if rest.get('can_proceed') and not any(o.get('is_enabled', True) for o in rest.get('options') or []):
            return program_move('篝火已盡其用，該上路了。\n建議：離開休息點。', 'proceed')
    if kind == 'treasure':
        treasure = state.get('treasure') or {}
        relics = treasure.get('relics')
        if isinstance(relics, list) and len(relics) == 1:
            return program_move('寶箱裡的東西，自然歸你。\n建議：拿取' + (relics[0].get('name') or '遺物') + '。',
                         'claim_treasure_relic', index=relics[0]['index'])
        if not relics and 'message' not in treasure and treasure.get('can_proceed'):
            return program_move('箱子空了，繼續前行。\n建議：離開寶箱房。', 'proceed')
    if kind == 'map':
        options = (state.get('map') or {}).get('next_options') or []
        if len(options) == 1:
            return program_move('只有一條路，那就走吧。\n建議：前往唯一可選的地點。', 'choose_map_node', index=options[0]['index'])
    if kind == 'event':
        event = state.get('event') or {}
        if event.get('in_dialogue'):
            return program_move('讓他說完。\n建議：繼續對話。', 'advance_dialogue')
        options = [o for o in event.get('options') or [] if not o.get('is_locked')]
        if len(options) == 1:
            return program_move('別無選擇，倒也省事。\n建議：選擇唯一可用的選項。', 'choose_event_option', index=options[0]['index'])
    if kind in ('monster', 'elite', 'boss'):
        hand = player.get('hand')
        potions = [p for p in player.get('potions') or [] if p.get('can_use_in_combat')]
        if isinstance(hand, list) and not any(card.get('can_play') for card in hand) and not potions:
            return program_move('手上已無可出之牌。\n建議：結束回合。', 'end_turn')
    return None


