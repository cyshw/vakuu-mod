"""Validated on-play relic effects shared by search and guarded queues."""
import copy
import re
from dataclasses import dataclass

# Audited v0.111.0 effects that have finished before the settled play phase.
# Their results are already in the hand, deck, block or powers; never add them twice.
@dataclass(frozen=True)
class RelicRule:
    description: str
    timing: str
    event: str
    effect: str
    amount: int = 0
    period: int = 0


# Timing and trigger are explicit. Random/hidden-state effects are boundaries,
# not claims that the simulator knows their outcome.
RELIC_RULES = {
    'ARCANE_SCROLL': RelicRule('拾起时，将一张随机稀有牌加入你的牌组。', 'pickup', 'settled_state', 'already_applied'),
    'ANCHOR': RelicRule('每场战斗开始时获得10点格挡。', 'combat_start', 'settled_state', 'already_applied'),
    'GORGET': RelicRule('在每场战斗开始时，获得4层覆甲。', 'combat_start', 'settled_state', 'already_applied'),
    'DIAMOND_DIADEM': RelicRule('战斗开始时获得20点格挡。你的格挡不会在你的第2回合开始前被移除。', 'combat_start', 'settled_state', 'already_applied'),
    'RADIANT_PEARL': RelicRule('在每场战斗开始时，将1张冷光加入你的手牌。', 'combat_start', 'settled_state', 'already_applied'),
    'BAG_OF_MARBLES': RelicRule('在每场战斗开始时，给予所有敌人1层易伤。', 'combat_start', 'settled_state', 'already_applied'),
    'VAJRA': RelicRule('在每场战斗开始时，获得1点力量。', 'combat_start', 'settled_state', 'already_applied'),
    'FRAGRANT_MUSHROOM': RelicRule('拾起时，失去15点生命，然后随机升级2张牌。', 'pickup', 'settled_state', 'already_applied'),
    'WAR_PAINT': RelicRule('拾起时，随机升级2张技能牌。', 'pickup', 'settled_state', 'already_applied'),
    'MUMMIFIED_HAND': RelicRule('你每打出一张能力牌，手牌中就有一张随机牌在这个回合可以免费打出。', 'after_card', 'Power', 'random_free_cost', amount=0),
    'UNCEASING_TOP': RelicRule('在你的回合，当你没有手牌时，抽一张牌。', 'after_card', 'hand_empty', 'draw_on_empty', amount=1),
    'CENTENNIAL_PUZZLE': RelicRule('你在每场战斗中第一次损失生命值时，抽3张牌。', 'damage_received', 'hp_lost_first_time', 'draw_on_damage', amount=3),
    'CLOAK_CLASP': RelicRule('在你的回合结束时，每有一张手牌，就获得1点格挡', 'before_hand_flush', 'turn_end', 'block_per_held_card', amount=1),
    'TUNING_FORK': RelicRule('你每打出10张技能牌，获得7点格挡。', 'after_card', 'Skill', 'periodic_block', amount=7, period=10),
}
# Compatibility views used by existing prompts/tests; one source for descriptions.
ENTRY_RELICS = {key: rule.description for key, rule in RELIC_RULES.items() if rule.effect == 'already_applied'}
CONDITIONAL_RELICS = {key: rule.description for key, rule in RELIC_RULES.items()
                      if rule.effect not in {'already_applied', 'periodic_block'}}


def relic_effects(ids):
    """Call after relic validation; only registered trigger kinds are returned."""
    return {RELIC_RULES[key].effect for key in ids if key in RELIC_RULES}


def held_card_block(ids, count):
    return sum(RELIC_RULES[key].amount * count for key in ids
               if key in RELIC_RULES and RELIC_RULES[key].effect == 'block_per_held_card')


def counted_card_indices(hand):
    event = RELIC_RULES['TUNING_FORK'].event
    return {card['index'] for card in hand if card.get('type') == event and 'index' in card}


def periodic_block(counter, cards_played):
    """Block from the current card; counter/played include only eligible skills."""
    rule = RELIC_RULES['TUNING_FORK']
    return rule.amount if counter is not None and (counter + cards_played) % rule.period == 0 else 0



def normalized(text):
    return re.sub(r'\s+', '', str(text or '')).rstrip('。.')


def audited_relic(relic):
    key = str(relic.get('id') or '').split('.')[-1]
    rule = RELIC_RULES.get(key)
    return bool(rule and rule.effect != 'periodic_block' and normalized(relic.get('description')) == normalized(rule.description)
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
    cloak_block = held_card_block({'CLOAK_CLASP'} if cloak else set(), count)
    return {'plating_block': plating, 'cloak_clasp_block': cloak_block,
            'block': plating + cloak_block, 'retain_block': retain}


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
    rule = RELIC_RULES['TUNING_FORK']
    if text != rule.description or type(counter) is not int or not 0 <= counter < rule.period:
        raise ValueError('unknown_tuning_fork')
    return counter


def after_card_relics(relics, card):
    result = copy.deepcopy(relics)
    counter = tuning_fork_counter(result)
    if counter is not None and card.get('type') == RELIC_RULES['TUNING_FORK'].event:
        for relic in result:
            if str(relic.get('id') or '').split('.')[-1] == 'TUNING_FORK':
                relic['counter'] = (counter + 1) % RELIC_RULES['TUNING_FORK'].period
    return result
