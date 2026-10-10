"""Daily card ratings from Spire Codex community runs (https://spire-codex.com, documented public API).

Ratings are fetched at most once a day per character and cached locally; when the site is unreachable
the last cached copy is used, so takeover never waits on or fails because of the network.
"""
import json
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / 'button-runtime' / 'spire-codex'
URL = 'https://spire-codex.com/api/runs/metrics/cards?bracket={bracket}&character={character}'
MAX_AGE = 24 * 3600
# A10+ plays closest to the user's high-ascension runs; "all" is dominated by low ascensions.
BRACKET = 'a10'
FIELDS = ('score', 'tier', 'win_rate', 'lift', 'pick_rate', 'picks')
_lock = threading.Lock()


def fetch(character, bracket):
    request = urllib.request.Request(URL.format(bracket=bracket, character=character.lower()),
                                     headers={'User-Agent': 'vakuu-mod/0.1 (personal STS2 assistant)'})
    with urllib.request.urlopen(request, timeout=8) as response:
        data = json.load(response)
    return {
        'data_through': data.get('data_through'), 'bracket': bracket, 'character': character.upper(),
        'baseline_win_rate': data.get('baseline_win_rate'), 'character_runs': data.get('character_runs'),
        'cards': {row['id']: {key: row.get(key) for key in FIELDS} for row in data['rows'] if row.get('id')},
    }


def card_metrics(character, bracket=BRACKET, now=time.time):
    """Cached metrics for one character, refreshed when older than a day; None if never fetched."""
    path = CACHE / f'cards-{bracket}-{character.lower()}.json'
    with _lock:
        try: cached = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError): cached = None
        if cached and now() - cached.get('fetched_at', 0) < MAX_AGE: return cached
        try:
            fresh = {**fetch(character, bracket), 'fetched_at': now()}
        except (OSError, ValueError, KeyError, TypeError):
            return cached  # Offline or API change: keep using yesterday's ratings.
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(fresh, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)
        return fresh
