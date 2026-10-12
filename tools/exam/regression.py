"""A fixed exam of recorded decision points, each with the checks its answer must pass.

replay_eval samples cases at random from logs that are not kept; this suite stores chosen states in
the repo (tests/regression/) so every prompt or model change is judged on the same questions. Models
answer differently each time, so each case can be asked several times and reported as a pass rate.

    python tools/exam/regression.py add run9#4 --expect "unblocked<=10" --note "火焰藥水能救命"
    python tools/exam/regression.py list
    python tools/exam/regression.py run --config claude:sonnet@low --repeat 2

Keeping it cheap (tools/exam/mine_exam.py adds tagged cases from the logs):
    run --config claude:haiku@low --quick 2 --retry 2   # small exam: 2 cases per tag, failures asked twice more
    run --tags potion rest                              # only what a change touched
A first attempt whose exact prompt was asked before reuses the cached answer (--no-cache to ask again).
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_eval  # noqa: E402

SUITE = Path(__file__).resolve().parents[2] / 'tests' / 'regression'


CHECKS = {'<=': lambda a, b: a is not None and a <= b, '>=': lambda a, b: a is not None and a >= b,
          '!=': lambda a, b: a != b, '=': lambda a, b: a == b}


def parse_expect(text):
    """'dies=false', 'unblocked<=10' or 'dies!=true' -> ('dies', ['=', False])."""
    op = next(op for op in CHECKS if op in text)
    key, _, value = text.partition(op)
    return key, [op, json.loads(value)]


def add(args):
    log, _, _ = args.case.partition('#')
    case = next((c for c in replay_eval.build_cases([replay_eval.LOGS / f'{log}.jsonl']) if c['id'] == args.case), None)
    if case is None: sys.exit(f'找不到 {args.case}（用 replay_eval 的案例編號，例如 run27-full#30）')
    route = played_route(replay_eval.LOGS / f'{log}.jsonl', case) if case['type'] == 'rest_site' else None
    SUITE.mkdir(parents=True, exist_ok=True)
    target = SUITE / (args.case.replace('#', '_') + '.json')
    target.write_text(json.dumps({'id': case['id'], 'type': case['type'], 'note': args.note,
                                  'expect': dict(parse_expect(e) for e in args.expect),
                                  'original': case['original'], 'state': case['state'],
                                  **({'route': route} if route else {})},
                                 ensure_ascii=False, indent=1), encoding='utf-8')
    print('saved', target, replay_eval.score(case, case['original']))


def played_route(path, case):
    """The route plan takeover held at this rest site, rebuilt from the last map decision before it.

    The rest site screen shows no map; rows are recovered from floors (row = floor - offset, with the
    offset read off the map screen where the route was drawn).
    """
    from route.plans import start_route
    drawn = None
    last = None
    for line in open(path, encoding='utf-8'):
        record = json.loads(line)
        if record['kind'] == 'state': last = record['state']
        elif record['kind'] == 'model' and last and last.get('state_type') == 'map' and record['decision'].get('next_nodes'):
            drawn = (last, record['decision'])
        if record['kind'] == 'model' and last is not None and replay_eval.fingerprint(last) == replay_eval.fingerprint(case['state']):
            break
    if not drawn: return None
    world, decision = drawn
    route = start_route(world, decision)
    options = (world.get('map') or {}).get('next_options') or []
    if not route or not options: return None
    offset = (world['run']['floor'] + 1) - options[0]['row']
    row = case['state']['run']['floor'] - offset
    return {'act': route['act'], 'hp_ratio': route['hp_ratio'], 'max_hp': route['max_hp'],
            'path': [list(n) for n in route['path'] if n[1] > row],
            'types': [[n[0], n[1], t] for n, t in route['types'].items()]}


def as_route(saved):
    """Back from JSON: node tuples and the (col, row) -> type map start_route builds."""
    if not saved: return None
    return {**saved, 'path': [tuple(n) for n in saved['path']], 'expected_hp': {},
            'types': {(c, r): t for c, r, t in saved['types']}}


def load():
    return [json.loads(p.read_text(encoding='utf-8')) for p in sorted(SUITE.glob('*.json'))]


def failures(case, decision):
    result = replay_eval.score(case, decision)
    wrong = []
    for key, check in case['expect'].items():
        op, value = check if isinstance(check, list) else ['=', check]
        if not CHECKS[op](result.get(key), value): wrong.append(f'{key}={result.get(key)!r}（應 {op} {value!r}）')
    return wrong


# Tags of cases written before mining tagged them, read off their checks.
CHECK_TAGS = {'potion_drunk_when_safe': 'potion', 'potion_held_when_lethal': 'potion_lethal', 'drank_potion': 'potion',
              'rested': 'rest', 'selected_strike': 'removal', 'wasted_energy_card': 'energy', 'picked_type': 'route',
              'picked_title': 'event', 'kills_all': 'kill', 'unblocked': 'combat', 'left_affordable_cards': 'combat'}
CACHE = replay_eval.LOGS.parent / 'exam-cache.json'


def tags_of(case):
    return case.get('tags') or sorted({CHECK_TAGS.get(key, 'other') for key in case['expect']})


def pick(args):
    cases = [c for c in load() if (not args.only or any(part in c['id'] for part in args.only))
             and (not args.tags or set(tags_of(c)) & set(args.tags))]
    if args.quick:  # A few cases per tag: a cheap check after each change.
        seen = {}
        cases = [c for c in cases if seen.setdefault(tags_of(c)[0], []).append(c) or len(seen[tags_of(c)[0]]) <= args.quick]
    return cases


def cached_calls(enabled):
    """Reuse a model answer when the exact prompt, schema and model were asked before (first attempts only)."""
    import hashlib
    import threading
    engine = replay_eval.engine
    raw = engine.Controller.call_model
    local = threading.local()
    lock = threading.Lock()
    try: store = json.loads(CACHE.read_text(encoding='utf-8')) if enabled else {}
    except (OSError, ValueError): store = {}
    hits = [0]

    def call_model(self, prompt, schema, selection, candidate=False):
        if not enabled or not getattr(local, 'use', False): return raw(self, prompt, schema, selection, candidate)
        key = hashlib.sha256(json.dumps([prompt, schema, selection, candidate], ensure_ascii=False).encode()).hexdigest()
        with lock: answer = store.get(key)
        if answer is not None:
            hits[0] += 1
            return answer
        answer = raw(self, prompt, schema, selection, candidate)
        with lock: store[key] = answer
        return answer
    engine.Controller.call_model = call_model

    def save():
        engine.Controller.call_model = raw
        if enabled:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(store, ensure_ascii=False), encoding='utf-8')
    return local, save, hits


def run(args):
    cases = pick(args)
    local, save, hits = cached_calls(not args.no_cache)

    def ask(job):
        case, attempt = job
        local.use = attempt == 0  # Repeats and retries ask the model afresh.
        try: return case, replay_eval.decide(args.config, case['state'], as_route(case.get('route'))), None
        except Exception as exc: return case, None, str(exc)

    passed = {}
    def answer(jobs):
        with ThreadPoolExecutor(args.workers) as pool:
            for case, decision, error in pool.map(ask, jobs):
                wrong = [error] if error else failures(case, decision)
                passed.setdefault(case['id'], []).append(not wrong)
                if wrong:
                    print(f"✗ {case['id']}：{'；'.join(wrong)}")
                    if decision: print('   ', decision['action'], decision['parameters_json'], decision['advice'].replace('\n', ' ')[:160])
    try:
        answer([(case, n) for case in cases for n in range(args.repeat)])
        failed = [c for c in cases if not all(passed[c['id']])]
        if args.retry and failed:  # Only cases that failed are asked again, to tell a fluke from a real miss.
            print(f'\n重考 {len(failed)} 題答錯的，各 {args.retry} 次')
            answer([(case, args.repeat + n) for case in failed for n in range(args.retry)])
    finally:
        save()
    total = sum(sum(v) for v in passed.values()); asked = sum(len(v) for v in passed.values())
    print(f'\n{args.config}：{total}/{asked} 通過（{len(cases)} 題，沿用快取 {hits[0]} 次）')
    for case in cases:
        print(f"  {sum(passed[case['id']])}/{len(passed[case['id']])}  [{','.join(tags_of(case))}] {case['id']}  {case['note']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('add'); p.add_argument('case'); p.add_argument('--note', default='')
    p.add_argument('--expect', action='append', default=[], help='score key=JSON value, e.g. dies=false')
    sub.add_parser('list')
    p = sub.add_parser('run'); p.add_argument('--config', default='claude:sonnet@low')
    p.add_argument('--repeat', type=int, default=1); p.add_argument('--workers', type=int, default=4)
    p.add_argument('--only', nargs='+', help='only cases whose id contains one of these, e.g. run28')
    p.add_argument('--tags', nargs='+', help='only these tags, e.g. potion rest (see list)')
    p.add_argument('--quick', type=int, metavar='N', help='at most N cases per tag')
    p.add_argument('--retry', type=int, default=0, metavar='N', help='ask failed cases N more times')
    p.add_argument('--no-cache', action='store_true', help='always ask the model, never reuse a cached answer')
    args = parser.parse_args()
    if args.command == 'add': add(args)
    elif args.command == 'list':
        for case in load(): print(f"[{','.join(tags_of(case))}]", case['id'], case['expect'], case['note'])
    else: run(args)


if __name__ == '__main__':
    main()
