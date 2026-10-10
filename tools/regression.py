"""A fixed exam of recorded decision points, each with the checks its answer must pass.

replay_eval samples cases at random from logs that are not kept; this suite stores chosen states in
the repo (tests/regression/) so every prompt or model change is judged on the same questions. Models
answer differently each time, so each case can be asked several times and reported as a pass rate.

    python tools/regression.py add run9#4 --expect "unblocked<=10" --note "火焰藥水能救命"
    python tools/regression.py list
    python tools/regression.py run --config claude:sonnet@low --repeat 2
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_eval  # noqa: E402

SUITE = Path(__file__).resolve().parent.parent / 'tests' / 'regression'


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
    from vakuu_plans import start_route
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


def run(args):
    cases = [c for c in load() if not args.only or any(part in c['id'] for part in args.only)]
    jobs = [(case, n) for case in cases for n in range(args.repeat)]

    def ask(job):
        case, _ = job
        try: return case, replay_eval.decide(args.config, case['state'], as_route(case.get('route'))), None
        except Exception as exc: return case, None, str(exc)

    passed = {}
    with ThreadPoolExecutor(args.workers) as pool:
        for case, decision, error in pool.map(ask, jobs):
            wrong = [error] if error else failures(case, decision)
            passed.setdefault(case['id'], []).append(not wrong)
            if wrong:
                print(f"✗ {case['id']}：{'；'.join(wrong)}")
                if decision: print('   ', decision['action'], decision['parameters_json'], decision['advice'].replace('\n', ' ')[:160])
    total = sum(sum(v) for v in passed.values())
    print(f'\n{args.config}：{total}/{len(jobs)} 通過')
    for case in cases:
        print(f"  {sum(passed[case['id']])}/{len(passed[case['id']])}  {case['id']}  {case['note']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('add'); p.add_argument('case'); p.add_argument('--note', default='')
    p.add_argument('--expect', action='append', default=[], help='score key=JSON value, e.g. dies=false')
    sub.add_parser('list')
    p = sub.add_parser('run'); p.add_argument('--config', default='claude:sonnet@low')
    p.add_argument('--repeat', type=int, default=1); p.add_argument('--workers', type=int, default=4)
    p.add_argument('--only', nargs='+', help='only cases whose id contains one of these, e.g. run28')
    args = parser.parse_args()
    if args.command == 'add': add(args)
    elif args.command == 'list':
        for case in load(): print(case['id'], case['type'], case['expect'], case['note'])
    else: run(args)


if __name__ == '__main__':
    main()
