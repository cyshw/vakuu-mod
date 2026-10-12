"""Validated on-play relic effects shared by search and guarded queues."""
import copy
import re

# Audited v0.111.0 effects that have finished before the settled play phase.
# Their results are already in the hand, deck, block or powers; never add them twice.
ENTRY_RELICS = {
    'ARCANE_SCROLL': '拾起时，将一张随机稀有牌加入你的牌组。',
    'ANCHOR': '每场战斗开始时获得10点格挡。',
    'GORGET': '在每场战斗开始时，获得4层覆甲。',
    'DIAMOND_DIADEM': '战斗开始时获得20点格挡。你的格挡不会在你的第2回合开始前被移除。',
    'RADIANT_PEARL': '在每场战斗开始时，将1张冷光加入你的手牌。',
    'BAG_OF_MARBLES': '在每场战斗开始时，给予所有敌人1层易伤。',
    'VAJRA': '在每场战斗开始时，获得1点力量。',
    'FRAGRANT_MUSHROOM': '拾起时，失去15点生命，然后随机升级2张牌。',
    'WAR_PAINT': '拾起时，随机升级2张技能牌。',
}
CONDITIONAL_RELICS = {
    'MUMMIFIED_HAND': '你每打出一张能力牌，手牌中就有一张随机牌在这个回合可以免费打出。',
    'UNCEASING_TOP': '在你的回合，当你没有手牌时，抽一张牌。',
    'CENTENNIAL_PUZZLE': '你在每场战斗中第一次损失生命值时，抽3张牌。',
    'CLOAK_CLASP': '在你的回合结束时，每有一张手牌，就获得1点格挡',
}


def normalized(text):
    return re.sub(r'\s+', '', str(text or '')).rstrip('。.')


def audited_relic(relic):
    key = str(relic.get('id') or '').split('.')[-1]
    expected = {**ENTRY_RELICS, **CONDITIONAL_RELICS}.get(key)
    return bool(expected and normalized(relic.get('description')) == normalized(expected)
                and relic.get('counter') is None)


def turn_end_defense(player, hand_count=None):
    """Known unpowered end block and block retention; no acquisition bonuses are added."""
    plating, retain = 0, False
    for power in player.get('status') or []:
        key = power.get('id')
        if key not in {'PLATING_POWER', 'BLUR_POWER'}: continue
        amount = power.get('amount')
        if type(amount) is not int or amount < 0: raise ValueError('unknown_end_turn_effect')
        text = normalized(power.get('description'))
        if key == 'PLATING_POWER':
            expected = f'在你的回合结束时获得{amount}点格挡。覆甲会在你的回合开始时减少1层'
            if text != expected: raise ValueError('unknown_end_turn_effect')
            plating += amount
        else:
            expected = f'你的下{amount}个回合开始时格挡不会消失'
            if text != expected: raise ValueError('unknown_end_turn_effect')
            retain |= amount > 0
    cloak = sum(1 for relic in player.get('relics') or []
                if relic.get('id') == 'CLOAK_CLASP' and audited_relic(relic))
    if cloak > 1: raise ValueError('duplicate_cloak_clasp')
    count = len(player.get('hand') or []) if hand_count is None else hand_count
    return {'plating_block': plating, 'cloak_clasp_block': cloak * count,
            'block': plating + cloak * count, 'retain_block': retain}


def compact_relics(compact, state):
    """Keep unknown prose, replace audited completed effects with their timing."""
    player = compact.get('player')
    if not isinstance(player, dict): return
    player['relics'] = [({'id': r['id'], 'name': r.get('name'), 'effect_already_in_state': True}
                         if r.get('id') in ENTRY_RELICS and audited_relic(r) else r)
                        for r in player.get('relics') or []]


def tuning_fork_counter(relics):
    forks = [r for r in relics if str(r.get('id') or '').split('.')[-1] == 'TUNING_FORK']
    if not forks: return None
    if len(forks) != 1: raise ValueError('duplicate_tuning_fork')
    relic = forks[0]
    text = re.sub(r'\s+', '', str(relic.get('description') or ''))
    counter = relic.get('counter')
    if text != '你每打出10张技能牌，获得7点格挡。' or type(counter) is not int or not 0 <= counter < 10:
        raise ValueError('unknown_tuning_fork')
    return counter


def after_card_relics(relics, card):
    result = copy.deepcopy(relics)
    counter = tuning_fork_counter(result)
    if counter is not None and card.get('type') == 'Skill':
        for relic in result:
            if str(relic.get('id') or '').split('.')[-1] == 'TUNING_FORK':
                relic['counter'] = (counter + 1) % 10
    return result
