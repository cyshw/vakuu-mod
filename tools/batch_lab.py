"""Sequential, explicitly launched combat diagnostics; preserve every result."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.random_lab import ROOT, formed_candidates, sample_pool


def request(url, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=90))


def verify_initial(spec, state):
    if any(c['upgrades'] not in (0, 1) for c in spec['deck']):
        raise ValueError('This verifier supports only zero or one upgrade')
    player = state['player']
    expected = Counter((c['id'], bool(c['upgrades'])) for c in spec['deck'])
    actual = Counter((c['id'], c['is_upgraded']) for c in player['deck'])
    if expected != actual:
        raise ValueError('Initial deck or upgrades differ from frozen configuration')
    if player['character_id'] != spec['character']:
        raise ValueError('Initial character differs from configuration')
    if set(c['id'] for c in player['relics']) != set(spec['relics']):
        raise ValueError('Initial relics differ from configuration')
    if player['hp'] != spec['hp'] or player['max_hp'] != spec['max_hp']:
        raise ValueError('Initial HP differs from configuration')
    if not state.get('battle', {}).get('enemies'):
        raise ValueError('No live encounter after setup')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=10)
    parser.add_argument('--model', default='claude:haiku')
    parser.add_argument('--effort', default='low', choices=('low', 'medium', 'high'))
    args = parser.parse_args()
    if not 1 <= args.count <= 20:
        parser.error('count must be 1..20')
    folder = ROOT / 'vakuu/button-runtime/lab' / ('formed-batch10-' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    summary = {'sampling': 'uniform_encounter', 'pool': 'formed',
               'model': args.model, 'effort': args.effort, 'results': [], 'status': 'running'}
    def save():
        (folder / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        (folder.parent / 'latest-batch.json').write_text(json.dumps({'folder': str(folder)}), encoding='utf-8')
    save()
    print('BATCH', folder, flush=True)
    pool = formed_candidates()
    game_url = 'http://127.0.0.1:15526/api/v1/singleplayer?format=json'
    try:
        for number in range(1, args.count + 1):
            if request('http://127.0.0.1:15527/status')['busy']:
                raise RuntimeError('Another panel controller is active')
            candidate = sample_pool(pool)
            spec = candidate['spec']
            start = request('http://127.0.0.1:15528/action', {'action': 'start', 'spec': spec})
            if start.get('error') or not start.get('attempt'):
                raise RuntimeError('Lab setup failed: ' + str(start.get('error')))
            time.sleep(2)
            request('http://127.0.0.1:15528/action', {'action': 'stop'})
            initial = request(game_url)
            config = {**candidate, 'attempt': start['attempt'], 'sampling': 'uniform_encounter'}
            (folder / f'{number:02d}-config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
            (folder / f'{number:02d}-initial.json').write_text(json.dumps(initial, ensure_ascii=False), encoding='utf-8')
            summary['active'] = {'number': number, 'character': spec['character'],
                                 'encounter': spec['encounter'], 'attempt': start['attempt']}
            save()
            verify_initial(spec, initial)
            print('START', json.dumps(summary['active']), flush=True)
            log = folder / f'{number:02d}-takeover.jsonl'
            with (folder / f'{number:02d}-console.txt').open('w', encoding='utf-8') as out:
                result = subprocess.run([sys.executable, str(ROOT / 'tools/takeover_log.py'),
                    '--steps', '100', '--model', args.model, '--effort', args.effort,
                    '--one-fight', '--log', str(log)], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
            state = request(game_url)
            events = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
            entry = {**summary['active'], 'floor': spec['floor'], 'state': state.get('state_type'),
                     'hp_before': spec['hp'], 'hp_after': state.get('player', {}).get('hp'),
                     'rounds': max((e['state']['battle']['round'] for e in events
                                    if e['kind'] == 'state' and e['state'].get('battle')), default=0),
                     'model_calls': sum(e['kind'] == 'model_call' for e in events),
                     'posts': sum(e['kind'] == 'post' for e in events),
                     'failed_posts': sum(e['kind'] == 'post' and e['result'].get('status') != 'ok' for e in events),
                     'model_seconds': round(sum(e.get('seconds', 0) for e in events if e['kind'] == 'model_call'), 2),
                     'return_code': result.returncode}
            entry['outcome'] = ('win' if entry['state'] in ('rewards', 'card_reward') else
                                'loss' if entry['state'] == 'game_over' else 'stopped')
            summary['results'].append(entry)
            summary.pop('active', None)
            save()
            print('RESULT', json.dumps(entry), flush=True)
            # Losses continue as requested; a stopped controller retains the original state for diagnosis.
            if entry['outcome'] == 'stopped' or result.returncode:
                raise RuntimeError('Controller stopped without finishing; inspect logs before continuing')
        summary['status'] = 'complete'
        save()
    except Exception as exc:
        summary.update(status='error', error=str(exc))
        save()
        raise


if __name__ == '__main__':
    main()
