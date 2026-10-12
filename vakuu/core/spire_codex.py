"""Daily card ratings from Spire Codex community runs (https://spire-codex.com, documented public API).

Ratings are fetched at most once a day per character and cached locally; when the site is unreachable
the last cached copy is used, so takeover never waits on or fails because of the network.
"""
import json
import re
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
from vakuu_paths import RUNTIME
CACHE = RUNTIME / 'spire-codex'
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


MONSTER_URL = 'https://spire-codex.com/api/{kind}/{id}?lang=zhs'
MONSTER_MAX_AGE = 7 * 24 * 3600  # Game data changes only with patches.
TAGS = re.compile(r'\[/?[a-z_]+\]')


def _get(kind, entity_id):
    request = urllib.request.Request(MONSTER_URL.format(kind=kind, id=entity_id),
                                     headers={'User-Agent': 'vakuu-mod/0.1 (personal STS2 assistant)'})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def _pattern(raw, names):
    """Attack pattern as readable lines: fixed cycles, random picks and conditional branches."""
    lines = []
    for state in (raw or {}).get('states') or []:
        kind = state.get('type')
        if kind == 'move':
            target = next((s for s in raw['states'] if s.get('id') == state.get('next')), None)
            following = names.get(target.get('move_id'), target.get('id')) if target and target.get('move_id') else (target or {}).get('id')
            lines.append(f"{names.get(state.get('move_id'), state.get('move_id'))} → {following or '結束'}")
        elif kind in ('random', 'conditional'):
            options = [names.get(b.get('move_id'), b.get('move_id')) + (f"（條件 {b['condition']}）" if b.get('condition') else '')
                       for b in state.get('branches') or []]
            lines.append(f"{state.get('id')}：{'隨機' if kind == 'random' else '依條件'}選 " + ('、'.join(options) if options else '（未列出）'))
    return {'type': (raw or {}).get('type'), 'initial': names.get((raw or {}).get('initial_move')), 'steps': lines}


def fetch_monster(monster_id, ascension):
    data = _get('monsters', monster_id)
    tough, deadly = ascension >= 8, ascension >= 9  # A8 raises enemy HP, A9 their damage.
    names = {m['id']: m.get('name') for m in data.get('moves') or []}
    def damage(d):
        if not d: return None
        hit = d.get('ascension') if deadly and d.get('ascension') is not None else d.get('normal')
        return f"{hit}×{d['hit_count']}" if d.get('hit_count') else hit
    abilities = []
    for power in data.get('innate_powers') or []:
        try: info = _get('powers', power['power_id'])
        except (OSError, ValueError): info = {}
        amount = power.get('amount_ascension') if deadly and power.get('amount_ascension') is not None else power.get('amount')
        # Descriptions are templates: [Amount] is this monster's amount; other numbers are literal.
        text = (info.get('description') or '').replace('[Amount]', str(amount))
        abilities.append({'name': info.get('name') or power['power_id'], 'amount': amount, 'description': TAGS.sub('', text)})
    hp = (data.get('min_hp_ascension'), data.get('max_hp_ascension')) if tough and data.get('min_hp_ascension') else (data.get('min_hp'), data.get('max_hp'))
    return {
        'name': data.get('name'), 'hp_range': list(hp), 'abilities': abilities,
        'moves': [{'name': m.get('name'), 'intent': m.get('intent'), 'damage': damage(m.get('damage')),
                   'block': (m.get('block') or {}).get('normal') if isinstance(m.get('block'), dict) else m.get('block'),
                   'effects': [f"{p.get('power_id')} {p.get('amount')}→{p.get('target')}" for p in m.get('powers') or []] or None}
                  for m in data.get('moves') or []],
        'pattern': _pattern(data.get('attack_pattern'), names),
        'encounters': [e.get('encounter_id') for e in data.get('encounters') or [] if e.get('encounter_id')],
    }


def monster_intel(monster_id, ascension, now=time.time):
    """Cached Spire Codex facts for one monster (HP, moves, move order, innate abilities), or None."""
    path = CACHE / 'monsters' / f'{monster_id}-a{ascension}.json'
    with _lock:
        try: cached = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError): cached = None
        fresh_enough = cached and now() - cached.get('fetched_at', 0) < MONSTER_MAX_AGE
        if fresh_enough and 'encounters' in cached['intel']: return cached['intel']
        try: intel = fetch_monster(monster_id, ascension)
        except (OSError, ValueError, KeyError, TypeError): return cached['intel'] if cached else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'fetched_at': now(), 'intel': intel}, ensure_ascii=False), encoding='utf-8')
        return intel


ENCOUNTER_URL = 'https://spire-codex.com/api/runs/encounter-stats?bracket={bracket}&page={page}&limit=100'


def encounter_stats(bracket=BRACKET, now=time.time):
    """Community damage taken and turns per encounter (overall and per character), cached daily."""
    path = CACHE / f'encounters-{bracket}.json'
    with _lock:
        try: cached = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError): cached = None
        if cached and now() - cached.get('fetched_at', 0) < MAX_AGE: return cached['encounters']
        try:
            rows, page = [], 1
            while True:
                request = urllib.request.Request(ENCOUNTER_URL.format(bracket=bracket, page=page),
                                                 headers={'User-Agent': 'vakuu-mod/0.1 (personal STS2 assistant)'})
                with urllib.request.urlopen(request, timeout=10) as response: data = json.load(response)
                rows += data.get('encounters') or []
                if not data.get('has_next') or page >= 20: break
                page += 1
        except (OSError, ValueError):
            return cached['encounters'] if cached else {}
        encounters = {row['encounter_id']: row for row in rows if row.get('encounter_id')}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'fetched_at': now(), 'encounters': encounters}, ensure_ascii=False), encoding='utf-8')
        return encounters



def room_damage(character, act, bracket=BRACKET):
    """Average HP lost per fight for this character in one act, by room type (weak pools apart).

    Weighted by how many runs met each encounter; None when Spire Codex has no data for that act.
    """
    totals = {}
    for encounter_id, row in (encounter_stats(bracket) or {}).items():
        if row.get('act') != act: continue
        mine = next((c for c in row.get('characters') or [] if c.get('character') == character), None)
        if not mine or not mine.get('total') or mine.get('avg_damage') is None: continue
        kind = row.get('room_type')
        if kind == 'monster' and encounter_id.endswith('_WEAK'): kind = 'monster_weak'
        weight, damage = totals.get(kind, (0, 0))
        totals[kind] = (weight + mine['total'], damage + mine['total'] * mine['avg_damage'])
    return {kind: round(damage / weight, 1) for kind, (weight, damage) in totals.items()} or None


def _cached(name, url, max_age, build, now=time.time):
    """One cached JSON document from Spire Codex, rebuilt by build(data) when older than max_age."""
    path = CACHE / f'{name}.json'
    with _lock:
        try: cached = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError): cached = None
        if cached and now() - cached.get('fetched_at', 0) < max_age: return cached['data']
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'vakuu-mod/0.1 (personal STS2 assistant)'})
            with urllib.request.urlopen(request, timeout=10) as response: data = build(json.load(response))
        except (OSError, ValueError, KeyError, TypeError):
            return cached['data'] if cached else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'fetched_at': now(), 'data': data}, ensure_ascii=False), encoding='utf-8')
        return data


def _plain(text):
    return TAGS.sub('', str(text or '')).strip()


def _events(rows):
    """Event id -> option title -> {id, next}: the options shown on the page that option leads to.

    The page text itself is story; what matters for a multi-step event is the choices that follow.
    """
    events = {}
    for event in rows:
        follow = {page.get('id'): [f"{_plain(o.get('title'))}：{_plain(o.get('description'))}" for o in page.get('options') or []]
                  for page in event.get('pages') or []}
        options = {}
        for option in [o for page in event.get('pages') or [] for o in page.get('options') or []] + (event.get('options') or []):
            title = _plain(option.get('title'))
            if title not in options: options[title] = {'id': option.get('id'), 'next': follow.get(option.get('id')) or []}
        events[event['id']] = options
    return events


def _event_metrics(data):
    metrics = {}
    for row in data['rows']:
        metrics.setdefault(row['event'], {})[row['option']] = {k: row.get(k) for k in ('share', 'win_rate', 'lift', 'chosen')}
    return {'data_through': data.get('data_through'), 'events': metrics}


def _relic_metrics(data):
    return {'baseline_win_rate': data.get('baseline_win_rate'), 'data_through': data.get('data_through'),
            'relics': {row['id']: {k: row.get(k) for k in ('score', 'win_rate', 'lift', 'pick_rate', 'picks')}
                       for row in data['rows'] if row.get('id')}}


def relic_metrics(character, bracket=BRACKET):
    return _cached(f'relics-{bracket}-{character.lower()}',
                   f'https://spire-codex.com/api/runs/metrics/relics?bracket={bracket}&character={character.lower()}',
                   MAX_AGE, _relic_metrics)


def relic_ids():
    """Chinese relic name -> id, since event and reward screens show relic names only."""
    return _cached('relic-names', 'https://spire-codex.com/api/relics?lang=zhs', MONSTER_MAX_AGE,
                   lambda rows: {_plain(r.get('name')): r['id'] for r in rows if r.get('id')}) or {}


def relic_rating(name, character):
    """Community rating of a relic by its shown name, or None."""
    rid = relic_ids().get(_plain(name))
    found = ((relic_metrics(character) or {}).get('relics') or {}).get(rid)
    return {'relic_id': rid, **found} if found else None


def event_advice(event_id, options, character):
    """Per shown option: what it leads to and how often the community picks it and wins afterwards.

    Win rates carry selection bias (strong runs take risky options), so they are a hint, not a verdict.
    Ancient events (Neow) offer relics and have no option metrics; their relic ratings stand in.
    """
    texts = (_cached('events-zhs', 'https://spire-codex.com/api/events?lang=zhs', MONSTER_MAX_AGE, _events) or {}).get(event_id) or {}
    metrics = ((_cached(f'event-metrics-{BRACKET}', f'https://spire-codex.com/api/runs/metrics/events?bracket={BRACKET}',
                        MAX_AGE, _event_metrics) or {}).get('events') or {}).get(event_id) or {}
    advice = []
    for option in options:
        if option.get('is_locked'): continue
        title = _plain(option.get('title'))
        known = texts.get(title) or {}
        entry = {'index': option.get('index'), 'title': title}
        if known.get('next'): entry['then_choose_from'] = known['next']
        if metrics.get(known.get('id')): entry['community'] = metrics[known['id']]
        elif option.get('relic_name'):
            rating = relic_rating(option['relic_name'], character)
            if rating: entry['relic_rating'] = rating
        if len(entry) > 2: advice.append(entry)
    return advice or None
