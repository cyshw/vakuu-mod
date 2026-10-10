"""Restart the current room: force-quit the game, relaunch it through Steam and press Continue.

The game saves when a room is entered, so this replays the room from its start (same enemies, intents
and opening hand), which allows comparing settings on the same fight. It also restarts the panel's
bridge so it loads the code on disk. A death deletes the save, so reload before a fight is lost.

    python tools/reload_room.py --save opening.json
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request

API = 'http://127.0.0.1:15526/api/v1/singleplayer'
STEAM_APP = 'steam://rungameid/2868840'
KILL = ("Get-Process SlayTheSpire2 -ErrorAction SilentlyContinue | Stop-Process -Force; "
        "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
        "Where-Object CommandLine -like '*spire-buttons*--bridge-server*' | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")


def state():
    try:
        return json.load(urllib.request.urlopen(API + '?format=json', timeout=5))
    except (OSError, ValueError):
        return None  # Game not up yet.


def post(body):
    request = urllib.request.Request(API, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(request, timeout=10))


def wait(predicate, timeout, label):
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = state()
        if current and predicate(current): return current
        time.sleep(1)
    sys.exit('timeout waiting for ' + label)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--save', help='Write the reloaded state to this JSON file')
    args = parser.parse_args()

    # Killing the bridge too: the mod only starts one at game launch, and a leftover would hold its port.
    subprocess.run(['powershell', '-NoProfile', '-Command', KILL], check=True)
    time.sleep(3)
    subprocess.run(['cmd', '/c', 'start', '', STEAM_APP], check=True)
    print('launched; waiting for the main menu', flush=True)
    # The menu is reported during the intro before Continue works; wait for the option and retry.
    wait(lambda s: s.get('state_type') == 'menu' and 'continue' in (s.get('options') or []), 240, 'main menu')
    for _ in range(10):
        result = post({'action': 'menu_select', 'option': 'continue'})
        if result.get('status') == 'ok': break
        time.sleep(2)
    else:
        sys.exit('Continue failed: ' + json.dumps(result, ensure_ascii=False))
    reloaded = wait(lambda s: s.get('state_type') not in ('menu', 'unknown', None), 120, 'the run')
    if args.save:
        with open(args.save, 'w', encoding='utf-8') as file: json.dump(reloaded, file, ensure_ascii=False)
    print('ready:', reloaded.get('state_type'), reloaded.get('run'), flush=True)


if __name__ == '__main__':
    main()
