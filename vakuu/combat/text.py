"""Shared display-text parsing. Numeric mentions alone do not certify a simulated effect."""
import re

DAMAGE_SINGLE = re.compile(r'造成(\d+)[点點][伤傷]害[。.]?')
BLOCK_SINGLE = re.compile(r'[获獲]得(\d+)[点點]格[挡擋][。.]?')
BLOCK = re.compile(r'[获獲]得\s*(\d+)\s*[点點]?格[挡擋][。.]?')
DAMAGE = re.compile(r'造成\s*(\d+)\s*[点點][伤傷]害(?:(\d+)次|(两次|兩次))?')
ENERGY = re.compile(r'[获獲]得(\d+)\[')


def described_damage(text):
    return sum(int(m[1]) * (int(m[2]) if m[2] else 2 if m[3] else 1)
               for m in DAMAGE.finditer(text or ''))


def described_block(text):
    return sum(int(m[1]) for m in BLOCK.finditer(text or ''))
