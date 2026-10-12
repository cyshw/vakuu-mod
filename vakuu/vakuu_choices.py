"""Candidate-only model selection and translation into program-owned actions."""
import json

from vakuu_facts import combat_facts
from vakuu_rules import COMBAT, END_TURN, validate
from vakuu_search import turn_candidates, card_move
from vakuu_relics import compact_relics

CHOICE_INSTRUCTIONS = '''你是殺戮尖塔2的戰鬥顧問。比較提供的候選，回傳 choice 的候選編號或 reject。
你看不到完整手牌；不要假設沒有其他選擇，也不要編造缺少的機制。
simulated 只計支援範圍內的當回合效果，partial 有未計效果，unverified 是待驗算的起手提案。
只選你能判斷合理的方案；資訊不足回 reject、reject_reason=insufficient_information；
有足夠資訊但方案不合適回 reject、reject_reason=no_suitable_plan。拒絕後會提供完整手牌。
選用時 reject_reason 留空；reason 用繁體中文簡述理由（60字內）。不需要重寫出牌順序。
考慮血量與敵人意圖；不要為普通戰鬥的小傷浪費藥水，菁英與頭目則可早用藥水。
end_turn=false 只有起手，抽牌後會重新決策。一般未打出的手牌回合結束會棄掉，保留／虛無等依描述處理。
判斷起手是否合理，不要求證明全手牌最優。
不要只因隱藏手牌可能更好而拒絕。若缺少資訊會影響候選的合法性、存活或關鍵機制，
或所有候選都明顯不合理，才拒絕；需要保留能量比較未知牌的效果時也可拒絕。'''


def choice_offer(state, search_result=None):
    if state.get('state_type') not in COMBAT: return None
    menu = turn_candidates(state, search_result)
    if not menu: return None
    if menu['search_reason'] in {'mixed_orb_order', 'unmodeled_end_turn_relic'}:
        # The API does not establish mixed orb order. Keep only the prefix of
        # known damage/block cards before any orb or unsupported effect.
        hand = {c.get('index'): c for c in (state.get('player') or {}).get('hand') or []}
        # A cheap unsupported card can sit first in every heuristic ordering.
        # Also propose pure supported cards directly, so it does not hide valid
        # multi-card defense/attack prefixes from the menu.
        known = [(c, card_move(c)) for c in hand.values() if c.get('can_play') is True]
        known = [(c, m) for c, m in known if m and not (m.channel or m.evokes or m.kill_only)]
        defenses = [(c, m) for c, m in known if m.block]
        attacks = [(c, m) for c, m in known if m.damage]
        enemies = [e for e in (state.get('battle') or {}).get('enemies') or [] if (e.get('hp') or 0) > 0]
        target = min(enemies, key=lambda e: (e.get('hp') or 0) + (e.get('block') or 0), default=None)
        proposals = []
        for style, cards in (('已知格擋起手', defenses + attacks), ('已知攻擊起手', attacks + defenses),
                             ('只打已知攻擊並保留餘能', attacks)):
            energy = (state.get('player') or {}).get('energy', 0)
            sequence = []
            for card, move in cards:
                if move.cost > energy or (move.damage and not target): continue
                action = {'card_index': card['index']}
                if move.damage: action['target'] = target['entity_id']
                sequence.append(action); energy -= move.cost
                if len(sequence) == 3: break
            if sequence: proposals.append({'style': style, 'sequence': sequence})
        plans, seen = [], set()
        for plan in proposals + menu['plans']:
            prefix = []
            for action in plan['sequence']:
                move = card_move(hand.get(action['card_index'], {}))
                if move is None or move.channel or move.evokes or move.kill_only: break
                prefix.append(action)
            signature = json.dumps(prefix, sort_keys=True)
            if not prefix or signature in seen: continue
            seen.add(signature)
            plans.append({'id': f'P{len(plans) + 1}', 'style': plan['style'], 'sequence': prefix,
                          'cards': [hand[a['card_index']].get('name') or hand[a['card_index']].get('id') for a in prefix],
                          'confidence': 'unverified', 'end_turn': False, 'requires_replan': True,
                          'unmodeled_effects': ['只含普通傷害／格擋起手；未評估充能球順序、回合結束遺物與後續回合。']})
        if not plans: return None
        menu = {**menu, 'plans': plans}
    # Missing/unknown rules need the full state. Random lightning targets are a
    # known uncertainty exposed by the orb/enemy payload; offer unverified
    # prefixes without inventing a damage forecast or auto-ending the turn.
    if menu['search_reason'] not in {'supported_turn', 'unsupported_hand', 'no_supported_moves',
                                     'needs_potion_evaluation', 'random_orb_targets', 'mixed_orb_order',
                                     'unmodeled_end_turn_relic'}:
        return None
    # Only advertise choices the translator can execute. Partial empty plans
    # cannot authorize ending a turn while unsupported cards remain.
    menu = {**menu, 'plans': [p for p in menu['plans'] if p['sequence'] or
                            (p.get('confidence') == 'simulated' and p.get('end_turn'))][:5]}
    if not menu['plans']: return None
    player = state.get('player') or {}
    facts = combat_facts(state)
    hp, max_hp = player.get('hp'), player.get('max_hp')
    if type(hp) is not int or type(max_hp) is not int or max_hp <= 0: return None
    if facts['lethal_if_no_more_block'] or hp < 0.3 * max_hp: return None
    # The menu has no potion actions. Do not hide a consequential potion choice.
    if facts['block_needed_for_no_damage'] > 0 and any(p.get('can_use_in_combat') for p in player.get('potions') or []):
        return None
    hand = {c.get('index'): c for c in player.get('hand') or []}
    referenced = {a['card_index'] for plan in menu['plans'] for a in plan['sequence']}
    payload = {
        'state_type': state['state_type'], 'run': state.get('run'),
        'player': {k: player[k] for k in ('hp', 'max_hp', 'energy', 'block', 'status', 'relics', 'orbs', 'potions') if k in player},
        'battle': state.get('battle'),
        'hand_count': len(hand), 'hidden_hand_count': len(hand) - len(referenced),
        'candidate_cards': [{k: hand[i][k] for k in ('index', 'id', 'name', 'cost', 'type', 'description', 'target_type') if k in hand[i]}
                            for i in sorted(referenced)],
        # These held curses can affect ending a partial plan, even without playing them.
        'held_end_effects': [c.get('description') for c in hand.values() if c.get('type') in {'Curse', 'Status'}],
        'plans': menu['plans'],
        **({'card_trigger_counts': facts['card_trigger_counts']} if facts.get('card_trigger_counts') else {}),
    }
    if any(t.get('id') == 'PANACHE_POWER' for t in facts.get('card_trigger_counts', [])):
        payload['player']['status'] = [s for s in player.get('status') or [] if s.get('id') != 'PANACHE_POWER']
    compact_relics(payload, state)
    schema = {'type': 'object', 'additionalProperties': False, 'properties': {
        'choice': {'type': 'string', 'enum': [p['id'] for p in menu['plans']] + ['reject']},
        'reject_reason': {'type': 'string', 'enum': ['', 'insufficient_information', 'no_suitable_plan']},
        'reason': {'type': 'string'}}, 'required': ['choice', 'reject_reason', 'reason']}
    return {'menu': menu, 'payload': payload, 'schema': schema,
            'prompt': CHOICE_INSTRUCTIONS + '\n局面與候選：' + json.dumps(payload, ensure_ascii=False)}


def chosen_decision(state, offer, answer):
    """Validate IDs and translate stored sequences; the model never supplies indices."""
    if not isinstance(answer, dict) or set(answer) != {'choice', 'reject_reason', 'reason'}:
        raise ValueError('候選回覆格式錯誤')
    if not isinstance(answer['reason'], str): raise ValueError('候選理由格式錯誤')
    if answer['choice'] == 'reject':
        if answer['reject_reason'] not in {'insufficient_information', 'no_suitable_plan'}:
            raise ValueError('候選拒絕原因錯誤')
        return None
    if answer['reject_reason'] != '': raise ValueError('選用候選不能同時拒絕')
    selected = next((p for p in offer['menu']['plans'] if p['id'] == answer['choice']), None)
    if selected is None: raise ValueError('未知候選編號')
    sequence = selected['sequence']
    if not sequence:
        if selected.get('confidence') != 'simulated' or not selected.get('end_turn'):
            raise ValueError('不完整方案不能直接結束回合')
        return {'advice': '〔AI 選擇 ' + selected['id'] + '〕\n建議：結束回合。' + answer['reason'][:120],
                'stop': False, 'action': 'end_turn', 'parameters_json': '{}', 'next_cards': [],
                'candidate_id': selected['id']}
    hand = {c.get('index'): c for c in (state.get('player') or {}).get('hand') or []}
    seen = set()
    for action in sequence:
        validate(state, 'play_card', action)
        index = action['card_index']
        if index in seen or index not in hand or not hand[index].get('can_play'):
            raise ValueError('候選包含無效手牌')
        seen.add(index)
    first = sequence[0]
    queue = [{'hand_index': a['card_index'], 'target': a.get('target', ''), 'potion_slot': -1} for a in sequence[1:]]
    # Partial proposals are prefixes, not permission to discard the remaining hand.
    if selected.get('confidence') == 'simulated' and selected.get('end_turn'):
        queue.append({'hand_index': END_TURN, 'target': '', 'potion_slot': -1})
    return {'advice': '〔AI 選擇 ' + selected['id'] + '〕\n建議：打出' +
                     (hand[first['card_index']].get('name') or hand[first['card_index']].get('id') or '候選首張牌') +
                     '。' + answer['reason'][:120],
            'stop': False, 'action': 'play_card', 'parameters_json': json.dumps(first),
            'next_cards': queue, 'candidate_id': selected['id']}
