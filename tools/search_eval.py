"""Measure local search coverage on recorded model calls, without any API/model calls.

Coverage is a counterfactual estimate, not a measured win rate or a full-game replay.
Usage: python -X utf8 tools/search_eval.py vakuu/button-runtime/logs/run28-full.jsonl
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'vakuu'))
from vakuu_rules import COMBAT
from vakuu_search import search_turn


def evaluate(paths):
    total, combat, covered, reasons, samples, times = 0, 0, 0, Counter(), [], []
    for path in paths:
        last, case = None, 0
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                record = json.loads(line)
                if record['kind'] == 'state': last = record['state']
                if record['kind'] != 'model': continue
                total += 1
                if not last or last.get('state_type') not in COMBAT: continue
                combat += 1; case += 1
                start = time.perf_counter()
                result = search_turn(last)
                times.append(1000 * (time.perf_counter() - start))
                reasons[result['reason']] += 1
                if result['decision'] is not None:
                    covered += 1
                    samples.append({'log': path.name, 'combat_call': case, 'floor': (last.get('run') or {}).get('floor'),
                                    'round': (last.get('battle') or {}).get('round'), **result})
    return {'model_calls': total, 'combat_calls': combat, 'local_candidates': covered,
            'estimated_fraction_of_all_calls': round(covered / total, 4) if total else 0,
            'fallback_and_success_reasons': dict(reasons),
            'max_search_ms': round(max(times, default=0), 2),
            'mean_search_ms': round(sum(times) / len(times), 2) if times else 0,
            'limitations': ['Counts recorded states only; changed actions change later states.',
                            'No game execution or empirical win-rate measurement.'], 'candidates': samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='+', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = evaluate(args.logs)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'candidates'}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
