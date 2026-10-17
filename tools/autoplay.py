"""Play run after run unattended: take over until the run ends, then start a new one from the menu.

Each takeover is logged by tools/takeover_log.py (one JSONL per run). A takeover that stops mid-run
(a guard tripped, a model error) is restarted on the same screen up to --retries times in a row;
after that, or on a screen this script does not know, it stops and leaves the game as it is.
Progress lines go to stdout and to <logs>/autoplay-status.log.

    python tools/autoplay.py --runs 3 --model claude:sonnet --effort low
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / 'vakuu' / 'button-runtime' / 'logs'
API = 'http://127.0.0.1:15526/api/v1/singleplayer'
# Menu buttons pressed, in order of preference, to get from wherever the menu is to a new run.
MENU_PATH = ('main_menu', 'singleplayer', 'standard')
DISMISS = ('ok', 'confirm', 'close', 'skip', 'proceed')


def state():
    for _ in range(10):
        try: return json.load(urllib.request.urlopen(API + '?format=json', timeout=5))
        except (OSError, ValueError): time.sleep(3)
    return None


def post(body):
    request = urllib.request.Request(API, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(request, timeout=10))


def say(text):
    line = time.strftime('%H:%M:%S ') + text
    print(line, flush=True)
    with open(LOGS / 'autoplay-status.log', 'a', encoding='utf-8') as status: status.write(line + '\n')


def options(screen):
    found = screen.get('options') or (screen.get('game_over') or {}).get('options') or []
    return [o if isinstance(o, str) else o.get('name') for o in found
            if isinstance(o, str) or o.get('enabled', True)]


def start_new_run(character):
    """Click through game over and the menus to a new run; True once the run has begun."""
    for _ in range(12):
        screen = state()
        if screen is None: say('遊戲沒有回應'); return False
        kind = screen.get('state_type')
        if kind not in ('menu', 'game_over'): return True
        names = [n.lower() for n in options(screen) if n]
        if character.lower() in names:
            post({'action': 'menu_select', 'option': character}); time.sleep(1)
            post({'action': 'menu_select', 'option': 'embark'}); time.sleep(6)
            continue
        choice = next((n for n in MENU_PATH + DISMISS if n in names), None)
        if choice is None:
            say(f'不認得的選單畫面，停止：{json.dumps(screen, ensure_ascii=False)[:300]}'); return False
        post({'action': 'menu_select', 'option': choice}); time.sleep(3)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--runs', type=int, default=3, help='new runs to start after the current one ends')
    parser.add_argument('--model', default='claude:sonnet')
    parser.add_argument('--effort', default='low')
    parser.add_argument('--character', default='DEFECT')
    parser.add_argument('--retries', type=int, default=3)
    parser.add_argument('--playbook', metavar='MODEL', help='model that writes the deck playbook, e.g. codex:gpt-6.1-sol@low')
    args = parser.parse_args()
    started, retries, last_floor = 0, 0, None
    log = LOGS / time.strftime('auto-%m%d-%H%M.jsonl')
    while True:
        screen = state()
        if screen is None: say('遊戲沒有回應，停止'); return
        if screen.get('state_type') in ('menu', 'game_over'):
            if started >= args.runs: say(f'已開 {started} 局，結束'); return
            if not start_new_run(args.character): return
            started += 1; retries = 0
            log = LOGS / time.strftime('auto-%m%d-%H%M.jsonl')
            say(f'開始第 {started} 局新遊戲，記錄 {log.name}')
            continue
        floor = (screen.get('run') or {}).get('floor')
        say(f'接手：第 {floor} 層 {screen.get("state_type")}，血 {(screen.get("player") or {}).get("hp")}')
        subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'tools' / 'takeover_log.py'), '--steps', '3000',
                        '--model', args.model, '--effort', args.effort, '--log', str(log)]
                       + (['--playbook', args.playbook] if args.playbook else []), cwd=ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        after = state() or {}
        kind, floor_now = after.get('state_type'), (after.get('run') or {}).get('floor')
        if kind == 'game_over' or kind == 'menu':
            say(f'這局結束：第 {floor_now if floor_now is not None else floor} 層（{kind}）')
            retries = 0; continue
        retries = retries + 1 if floor_now == last_floor else 1
        last_floor = floor_now
        tail = [json.loads(l) for l in open(log, encoding='utf-8')][-6:] if log.exists() else []
        reason = next((r['message'].replace('\n', ' ') for r in reversed(tail) if r.get('kind') == 'report'
                       and r['message'] != '__DONE__'), '')
        say(f'接手中途停下（第 {floor_now} 層 {kind}，第 {retries} 次）：{reason[:160]}')
        if retries > args.retries: say('同一層連續停下太多次，停止，留給人處理'); return
        time.sleep(5)


if __name__ == '__main__':
    main()
