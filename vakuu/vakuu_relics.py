"""Validated on-play relic effects shared by search and guarded queues."""
import copy
import re


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
