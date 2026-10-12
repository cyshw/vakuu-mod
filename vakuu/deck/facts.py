"""Deck summaries and the block/damage profile used on card and shop screens."""
import re

def deck_summary(deck):
    """One entry per distinct card with a count, so the model knows what each card does without repeats."""
    groups = {}
    for card in deck:
        key = (card.get('id'), card.get('name'), card.get('is_upgraded'))
        if key in groups: groups[key]['count'] += 1
        else: groups[key] = {'name': card.get('name'), 'count': 1, 'cost': card.get('cost'),
                             'upgraded': bool(card.get('is_upgraded')), 'description': card.get('description')}
    return list(groups.values())

BLOCK_TEXT = re.compile(r'[获獲]得\s*(\d+)\s*点?[格]挡')

STARTERS = ('STRIKE', 'DEFEND')

# Block cards besides Defend a deck should have by each act; run 28 reached the act 2 boss with 3 of 27
# cards and died to a 57-damage turn with no block in hand.
BLOCK_TARGET = {1: 2, 2: 4, 3: 5}

def deck_profile(deck, act=None):
    """What the deck has and lacks, so card choices consider needs and not only general card ratings.

    A run died with 4 Strikes and 4 Defends after skipping block cards rated "F": ratings say how strong
    a card is in general, not whether this deck is short of block.
    """
    count = lambda pred: sum(1 for card in deck if pred(card))
    text = lambda card: str(card.get('description') or '')
    block = count(lambda c: (BLOCK_TEXT.search(text(c)) or '冰霜' in text(c)) and not str(c.get('id', '')).startswith('DEFEND'))
    target = BLOCK_TARGET.get(act)
    return {
        'size': len(deck),
        **({'block_target_besides_defend': target, 'short_of_block': block < target} if target else {}),
        'attacks': count(lambda c: c.get('type') == 'Attack'),
        'skills': count(lambda c: c.get('type') == 'Skill'),
        'powers': count(lambda c: c.get('type') == 'Power'),
        'block_sources': count(lambda c: BLOCK_TEXT.search(text(c)) or '冰霜' in text(c)),
        'block_sources_besides_defend': block,
        'starter_strikes': count(lambda c: str(c.get('id', '')).startswith('STRIKE')),
        'starter_defends': count(lambda c: str(c.get('id', '')).startswith('DEFEND')),
        'unplayable': count(lambda c: '不能被打出' in text(c)),
    }
