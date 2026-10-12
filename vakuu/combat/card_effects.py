"""Audited card rules: exact display text, target, and ordered supported effects.

Adding an ID here does not add a new mechanic. The simulator must implement it
before a rule may describe that mechanic; unknown suffixes still decline.
"""
import re
from dataclasses import dataclass
from combat.text import DAMAGE_SINGLE, BLOCK_SINGLE


@dataclass(frozen=True)
class Capture:
    group: int


@dataclass(frozen=True)
class CardRule:
    target: str
    pattern: str
    effects: tuple


@dataclass(frozen=True)
class Move:
    index: int
    cost: int
    damage: int = 0
    block: int = 0
    channel: str = ''
    evokes: int = 0
    kill_only: bool = False
    hits: int = 1
    vulnerable: int = 0
    exhaust: bool = False


STRIKES = {'STRIKE_' + c for c in ('IRONCLAD', 'SILENT', 'DEFECT', 'NECROBINDER', 'REGENT')}
DEFENDS = {'DEFEND_' + c for c in ('IRONCLAD', 'SILENT', 'DEFECT', 'NECROBINDER', 'REGENT')}
STRIKE_RULE = CardRule('AnyEnemy', DAMAGE_SINGLE.pattern, (('damage', Capture(1)),))
DEFEND_RULE = CardRule('Self', BLOCK_SINGLE.pattern, (('block', Capture(1)),))
CARD_RULES = {
    **dict.fromkeys(STRIKES | {'SLICE'}, STRIKE_RULE),
    **dict.fromkeys(DEFENDS | {'DEFLECT'}, DEFEND_RULE),
    'SHIV': CardRule('AnyEnemy', r'造成(\d+)点伤害。消耗。',
                     (('damage', Capture(1)), ('exhaust', True))),
    'TWIN_STRIKE': CardRule('AnyEnemy', r'造成(\d+)点伤害两次。',
                           (('damage', Capture(1)), ('hits', 2))),
    'BASH': CardRule('AnyEnemy', r'造成(\d+)点伤害。给予(\d+)层易伤。',
                    (('damage', Capture(1)), ('vulnerable', Capture(2)))),
    'ZAP': CardRule('Self', r'生成1个闪电充能球。', (('channel', 'LIGHTNING_ORB'),)),
    'DUALCAST': CardRule('Self', r'激发你最右侧的充能球两次。', (('evokes', 2),)),
    'BALL_LIGHTNING': CardRule('AnyEnemy', r'造成(\d+)点伤害。生成1个闪电充能球。',
                              (('damage', Capture(1)), ('channel', 'LIGHTNING_ORB'))),
    # Future-turn benefits remain unsupported; these moves only authorize kills.
    'ADAPTIVE_STRIKE': CardRule('AnyEnemy',
        r'造成(\d+)点伤害。将这张牌的一张0\[defect_energy_icon.png\]复制品添加到你的弃牌堆。',
        (('damage', Capture(1)), ('kill_only', True))),
    'MOMENTUM_STRIKE': CardRule('AnyEnemy',
        r'造成(\d+)点伤害。这张牌的耗能降为0\[defect_energy_icon.png\]。',
        (('damage', Capture(1)), ('kill_only', True))),
}


def card_move(card):
    rule = CARD_RULES.get(str(card.get('id') or '').split('.')[-1])
    if rule is None or card.get('target_type') != rule.target: return None
    if type(card.get('index')) is not int or card.get('star_cost') not in (None, 0): return None
    try: cost = int(card['cost'])
    except (KeyError, TypeError, ValueError): return None
    if cost < 0: return None
    text = re.sub(r'\s+', '', str(card.get('description') or ''))
    match = re.fullmatch(rule.pattern, text)
    if match is None: return None
    fields = {effect: int(match[value.group]) if isinstance(value, Capture) else value
              for effect, value in rule.effects}
    return Move(card['index'], cost, **fields)


INERT_TEXT = {'ASCENDERS_BANE': '不能被打出。虚无。永恒。',
              'CLUMSY': '不能被打出。虚无。', 'DAZED': '不能被打出。虚无。',
              'INJURY': '不能被打出。'}
INERT_HAND = set(INERT_TEXT)


def inert_ethereal_count(hand):
    return sum(card.get('can_play') is False
               and (text := re.sub(r'\s+', '', str(card.get('description') or ''))) == INERT_TEXT.get(str(card.get('id') or '').split('.')[-1])
               and '虚无。' in text for card in hand)
