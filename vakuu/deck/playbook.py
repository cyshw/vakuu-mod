"""How this deck is played, written once per deck by a stronger model and shown on every combat turn.

Haiku lost a rebuilt act 1 boss fight the player won with the same deck: it held Unmovable (doubles
the first block each turn) for eight turns, played Colossus on enemies without Vulnerable, and spent
Bash without the cards that pay off Vulnerable. Each was a question of how the deck works as a whole,
which a turn-by-turn view of the hand does not show.
"""
import hashlib
import json

from core.paths import RUNTIME
from deck.facts import deck_summary

CACHE = RUNTIME / 'playbooks'
# The panel's takeover writes playbooks with this model; exams and tools pass their own or none.
DEFAULT_MODEL = 'codex:gpt-6.1-sol@low'
_failed = set()  # Deck keys whose playbook call failed in this process: not retried every turn.
SCHEMA = {'type': 'object', 'additionalProperties': False,
          'properties': {'playbook': {'type': 'string'}}, 'required': ['playbook']}
INSTRUCTIONS = ('你是殺戮尖塔2的高手。以下是玩家目前的角色、牌組與遺物（JSON）。用繁體中文寫這副牌在戰鬥中的打法，'
                '條列 3 到 6 點、合計 250 字內，給之後每回合出牌的較弱模型照著做：哪些是核心牌與組合（type 為 Power 的能力牌打出後整場有效）；'
                '出牌順序原則（哪些能力牌抽到就先打、哪些牌要先上狀態或先疊格擋再打）；格擋與輸出怎麼取捨；'
                '這副牌常見的錯誤。只寫這副牌通用的打法，不寫特定敵人；牌名照 JSON 裡的寫法。')


def deck_key(state):
    """Same deck, upgrades and relics: same playbook."""
    player = state.get('player') or {}
    cards = sorted(f"{c.get('id')}{'+' if c.get('is_upgraded') else ''}" for c in player.get('deck') or [])
    relics = sorted(str(r.get('id') or r.get('name')) for r in player.get('relics') or [])
    raw = json.dumps([player.get('character_id'), cards, relics])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def playbook_prompt(state):
    player = state.get('player') or {}
    kinds = {c.get('name'): c.get('type') for c in player.get('deck') or []}
    body = {'character': player.get('character') or player.get('character_id'),
            'act': (state.get('run') or {}).get('act'),
            # Card types matter: Unmovable read as a one-turn effect until the playbook saw it is a Power.
            'deck': [{**row, 'type': kinds.get(row['name'])} for row in deck_summary(player.get('deck') or [])],
            'relics': [{'name': r.get('name'), 'description': r.get('description')} for r in player.get('relics') or []]}
    return INSTRUCTIONS + '\n' + json.dumps(body, ensure_ascii=False)


def deck_playbook(state, ask):
    """The cached playbook for this deck, asking ask(prompt, schema) for one when the deck is new.

    None when the state has no deck or the model call fails; the turn then goes on without it.
    """
    if not (state.get('player') or {}).get('deck'): return None
    key = deck_key(state)
    path = CACHE / f'{key}.json'
    try: return json.loads(path.read_text(encoding='utf-8'))['playbook']
    except (OSError, ValueError, KeyError): pass
    if key in _failed: return None
    try: answer = ask(playbook_prompt(state), SCHEMA)
    except (RuntimeError, ValueError, OSError):
        _failed.add(key)  # Not logged in or out of quota: play on without it rather than wait each turn.
        return None
    text = str((answer or {}).get('playbook') or '').strip()
    if not text: return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'playbook': text}, ensure_ascii=False), encoding='utf-8')
    return text
