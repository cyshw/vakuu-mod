"""Validated trigger counters; unfamiliar descriptions retain the full-state fallback."""
import copy
import re
from combat.card_effects import inert_ethereal_count

PANACHE = re.compile(r'如果你在本回合再打出(\d+)[张張]牌，就[对對]所有[敌敵]人造成(\d+)[点點][伤傷]害。')
PANACHE_CARD = re.compile(r'每[当當]你在一回合[内內]打出五[张張]牌[时時]，[对對]所有[敌敵]人造成(\d+)[点點][伤傷]害。')


# Existing modifiers are read from the settled state; activation is not simulated.
POWER_RULES = {
    'AFTERIMAGE_POWER': ('你每打出一张牌，都获得{amount}点格挡。', 'block_per_card'),
    'FEEL_NO_PAIN_POWER': ('每当有一张牌被消耗时，获得{amount}点格挡。', 'block_per_exhaust'),
    'ACCURACY_POWER': ('小刀造成{amount}点额外伤害。', 'already_in_damage_text'),
}


def card_trigger_values(player):
    result = {'block_per_card': 0, 'block_per_exhaust': 0, 'already_in_damage_text': 0}
    for power in player.get('status') or []:
        rule = POWER_RULES.get(power.get('id'))
        if rule is None: continue
        amount = power.get('amount')
        if type(amount) is not int or amount < 0:
            raise ValueError('unknown_card_trigger')
        text = re.sub(r'\s+', '', str(power.get('description') or ''))
        if text != rule[0].format(amount=amount): raise ValueError('unknown_card_trigger')
        result[rule[1]] += amount
    return result


def end_hand_exhaust_block(player):
    return inert_ethereal_count(player.get('hand') or []) * card_trigger_values(player)['block_per_exhaust']


def panache_counters(player):
    counters = []
    for power in player.get('status') or []:
        if power.get('id') != 'PANACHE_POWER': continue
        match = PANACHE.fullmatch(re.sub(r'\s+', '', str(power.get('description') or '')))
        if not match or type(power.get('amount')) is not int:
            raise ValueError('unknown_panache_counter')
        remaining, damage = map(int, match.groups())
        if remaining != power['amount'] or not 1 <= remaining <= 5 or damage < 0:
            raise ValueError('unknown_panache_counter')
        counters.append((remaining, damage))
    return tuple(counters)


def after_card_triggers(statuses, card):
    result = copy.deepcopy(statuses)
    counters = panache_counters({'status': statuses})
    if card.get('id') == 'PANACHE':
        # A new instanced Power's exact state comes from the API; replan after activation.
        return result
    index = 0
    for power in result:
        if power.get('id') != 'PANACHE_POWER': continue
        remaining, _ = counters[index]
        index += 1
        remaining = remaining - 1 or 5
        power['amount'] = remaining
        power['description'] = re.sub(r'(再打出\s*)\d+', lambda m: m[1] + str(remaining), power['description'])
    return result


def trigger_facts(player):
    result = []
    try:
        counters = panache_counters(player)
    except ValueError:
        counters = ()
    for left, damage in counters:
        result.append({'id': 'PANACHE_POWER', 'cards_left': left, 'damage_all': damage,
                       'potions_count': False, 'powered_attack': False})
    try:
        values = card_trigger_values(player)
    except ValueError:
        values = None
    if values is not None:
        for power in player.get('status') or []:
            if power.get('id') in POWER_RULES:
                effect = POWER_RULES[power['id']][1]
                result.append({'id': power['id'], 'effect': effect, 'amount': power['amount'],
                               'potions_count': False, 'powered_block': False})
    for card in player.get('hand') or []:
        if card.get('id') == 'PANACHE' and PANACHE_CARD.fullmatch(re.sub(r'\s+', '', str(card.get('description') or ''))):
            result.append({'card_index': card.get('index'), 'id': 'PANACHE',
                           'cards_after_activation': 5, 'activation_counts': False})
    return result


def compact_triggers(compact, state):
    try:
        counters = panache_counters(state.get('player') or {})
    except ValueError:
        return
    if counters:
        player = compact.get('player') or {}
        player['status'] = [s for s in player.get('status') or [] if s.get('id') != 'PANACHE_POWER']
        # combat_facts contains the validated numeric replacement; do not repeat the prose.
        effects = compact.get('combat_facts', {}).get('timed_effects') or []
        names = {s.get('name') for s in (state.get('player') or {}).get('status') or [] if s.get('id') == 'PANACHE_POWER'}
        compact['combat_facts']['timed_effects'] = [e for e in effects if e.get('name') not in names]

    try:
        card_trigger_values(state.get('player') or {})
    except ValueError:
        return
    player = compact.get('player') or {}
    summarized = {row.get('id') for row in (compact.get('combat_facts') or {}).get('card_trigger_counts') or []}
    player['status'] = [s for s in player.get('status') or [] if s.get('id') not in POWER_RULES or s.get('id') not in summarized]
