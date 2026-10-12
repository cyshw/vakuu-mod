"""Small, evidence-based deck context, separate from population card ratings.

Counts describe coverage, not expected turn damage or a win-rate prediction.
Unknown descriptions remain visible rather than receiving invented effects.
"""
import re
from collections import Counter


def card_roles(card):
    text = re.sub(r'\s+', '', str(card.get('description') or ''))
    roles = set()
    if re.search(r'造成\d+点伤害', text): roles.add('damage')
    if re.search(r'获得\d+点格挡', text) or '冰霜充能球' in text: roles.add('block')
    if re.search(r'抽(?:\d+|一|两)张牌', text): roles.add('draw')
    if '一种不同的充能球' in text and '抽' in text: roles.add('orb_draw')
    if re.search(r'获得(?:\[defect_energy_icon.png\])+', text): roles.add('energy')
    if '生成' in text and '充能球' in text: roles.add('channel')
    if re.search(r'获得\d+点?集中', text) and '在本回合' not in text: roles.add('focus')
    if '回合开始时' in text and '充能球的被动能力' in text: roles.add('orb_payoff')
    if '每当你打出一张能力牌' in text: roles.add('power_payoff')
    if card.get('type') == 'Power': roles.add('power')
    if str(card.get('cost')) == '0' and card.get('type') in {'Attack', 'Skill'}:
        roles.add('zero_cost')
    if str(card.get('id', '')).split('.')[-1] == 'ALL_FOR_ONE': roles.add('zero_payoff')
    return roles


def deck_direction(state, offered=(), archetype_hint=None):
    player = state.get('player') or {}
    deck = player.get('deck') or []
    counts = Counter(role for c in deck for role in card_roles(c))
    nonstarter_attacks = sum(c.get('type') == 'Attack' and
                            not str(c.get('id', '')).split('.')[-1].startswith('STRIKE') for c in deck)
    # A coverage warning, not an attack-count veto: an orb deck can deal damage
    # without Attack cards. Scaling/channel engines require a model's assessment.
    early = (state.get('run') or {}).get('act') == 1
    supported_engine = ((counts['focus'] or counts['orb_payoff']) and counts['channel'] >= 2 or
                        counts['power_payoff'] and counts['power'] >= 3 or
                        counts['zero_payoff'] and counts['zero_cost'] >= 2)
    fragile_damage = early and nonstarter_attacks < 3 and not supported_engine
    needs = []
    if fragile_damage: needs.append('damage')
    nonstarter_block = sum('block' in card_roles(c) and
                           not str(c.get('id', '')).split('.')[-1].startswith('DEFEND') for c in deck)
    if nonstarter_block < {1: 2, 2: 4, 3: 5}.get((state.get('run') or {}).get('act'), 2): needs.append('block')
    if len(deck) >= 18 and counts['draw'] < 2: needs.append('draw')
    directions = []
    for name, enablers, payoffs in (
        ('充能球／集中', counts['channel'], counts['focus'] + counts['orb_payoff']),
        ('能力牌觸發', counts['power'], counts['power_payoff']),
        ('零費回收', counts['zero_cost'], counts['zero_payoff']),
    ):
        if payoffs or (name == '充能球／集中' and enablers >= 2):
            directions.append({'name': name, 'support_cards': enablers, 'payoff_cards': payoffs,
                               'stage': ('可發展，仍缺成長' if not payoffs else
                                         '已有配合' if enablers >= 2 else '尚缺配合')})
    context = {
        'archetype_hint': archetype_hint, 'directions': directions,
        # Sorted: card_roles returns sets, whose order changes per process and broke the exam cache.
        'coverage': dict(sorted(counts.items())), 'nonstarter_attacks': nonstarter_attacks,
        'cost_curve': dict(Counter(str(c.get('cost')) if c.get('cost') is not None else 'unknown'
                                  for c in deck if c.get('type') in {'Attack', 'Skill', 'Power'})),
        'upgraded_cards': sum(c.get('is_upgraded') is True for c in deck),
        'needs': needs, 'protect_starter_damage': fragile_damage,
        'relic_ids': [r.get('id') for r in player.get('relics') or []],
        'unknown_description_count': sum(not c.get('description') for c in deck),
        'skip_guidance': '不補缺口、不增加已有配合且只會稀釋關鍵牌時可跳過；有缺口也不必拿無效的牌。',
        'limitations': '描述計數只標示覆蓋與配合，不是平均每回合傷害；未模擬能量、抽牌順序或遺物效果。沒有方向證據時先補生存／輸出，不硬湊流派。',
    }
    fits = {}
    existing = Counter(str(c.get('id', '')).split('.')[-1] for c in deck)
    for card in offered:
        roles = card_roles(card)
        fit = {'roles': sorted(roles), 'fills_needs': sorted(roles & set(needs)),
               'copies_owned': existing[str(card.get('id', '')).split('.')[-1]], 'synergies': []}
        if ('channel' in roles and (counts['focus'] or counts['orb_payoff'])) or (roles & {'focus', 'orb_payoff'} and counts['channel']):
            fit['synergies'].append('現有充能球與集中配合')
        if ('channel' in roles and counts['orb_draw']) or ('orb_draw' in roles and counts['channel']):
            fit['synergies'].append('球種類與抽牌配合，需比較是否增加不同種類')
        if ('power' in roles and counts['power_payoff']) or ('power_payoff' in roles and counts['power'] >= 2):
            fit['synergies'].append('現有能力牌觸發配合')
        if ('zero_cost' in roles and counts['zero_payoff']) or ('zero_payoff' in roles and counts['zero_cost'] >= 2):
            fit['synergies'].append('現有零費回收配合')
        fit['effect_known'] = bool(card.get('description'))
        fits[card['index']] = fit
    return context, fits
