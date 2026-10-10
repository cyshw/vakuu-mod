"""Vakuu takeover engine and the HTTP bridge used by the in-game panel."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import sts2_control as api
from sts2_advisor import advise
import spire_models as models
import spire_quota
from vakuu_facts import combat_facts, deck_profile, deck_summary, enemy_ids, enemy_notes, pile_contents, pull_keywords, rest_facts, route_ahead, route_facts, wastes_energy_card
from spire_codex import event_advice, monster_intel
from vakuu_plans import auto_decision, first_card, plan_decision, route_decision, start_plan, start_route
from vakuu_router import fight_key, hard_screen, undecided
from vakuu_search import search_turn, turn_candidates
from vakuu_rules import (COMBAT, END_TURN, FIELDS, LEAVE_SHOP, SCHEMA, allowed_actions, fingerprint, still_valid,
                         transitional, validate)

# Tuned for the game's Instant fast mode; wait_until_settled covers screens that still animate.
ACTION_PAUSE = 0.5
POLL_INTERVAL = 0.25

def prune_runs(keep=40):
    # Each call leaves a schema/log folder; keep only recent ones for debugging.
    runs = sorted((p for p in (ROOT / 'button-runtime').glob('*-*-*-*-*') if p.is_dir()),
                  key=lambda p: p.stat().st_mtime)
    for old in runs[:-keep]: shutil.rmtree(old, ignore_errors=True)

PROMPT = ROOT / 'vakuu_prompt.md'

def instructions():
    """Model instructions, read on every call so edits apply without restarting the game."""
    text = PROMPT.read_text(encoding='utf-8')
    return text.replace('{{END_TURN}}', str(END_TURN)).replace('{{LEAVE_SHOP}}', str(LEAVE_SHOP)).strip()

class Controller:
    def __init__(self, report, allow_model=True):
        self.report = report
        self.allow_model = allow_model
        self.stop_event = threading.Event()
        self.process = None
        self.lock = threading.Lock()
        self.strategy = None  # {'fight': fight_key, 'text': battle plan} in auto:brief mode.
        self.route = None  # Planned map route; kept across takeovers until something calls for a re-plan.
        self.want_strategy = False
        self.local_search = None  # (state fingerprint, result), shared with the model prompt.

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()

    def wait_for_play_phase(self, state):
        deadline = time.monotonic() + 45
        announced = False
        while state.get('state_type') in {'monster', 'elite', 'boss'}:
            battle = state.get('battle', {})
            if battle.get('is_play_phase') is not False and battle.get('turn') != 'enemy':
                break
            if not announced:
                self.report('契約還沒結束。讓對手先出手。\n正在等待敵方回合與動畫結算，接手會繼續。')
                announced = True
            if time.monotonic() >= deadline:
                raise RuntimeError('等待出牌階段超過 45 秒，已停止；請確認遊戲是否需要手動處理。')
            if self.stop_event.wait(POLL_INTERVAL): raise InterruptedError('已停止')
            state = api.request('/api/v1/singleplayer?format=json')
        return state

    def wait_for_change(self, before, timeout=5):
        # Some actions (e.g. map travel) keep showing the old screen for a moment; acting on it
        # repeats the move or trips the stale-state check.
        # Effects also land in stages (Capacitor updates the hand first and orb slots later), so after
        # the first change keep reading until two reads in a row agree.
        deadline = time.monotonic() + timeout
        if self.stop_event.wait(ACTION_PAUSE): return
        previous = fingerprint(before)
        changed = False
        while time.monotonic() < deadline:
            current = fingerprint(api.request('/api/v1/singleplayer?format=json'))
            if changed and current == previous: return
            changed = changed or current != previous
            previous = current
            if self.stop_event.wait(POLL_INTERVAL): return

    def wait_until_settled(self, state, timeout=10):
        # Right after resting or claiming the last reward, the screen has nothing to choose and the
        # proceed button is still animating in; asking the model then makes it give up.
        deadline = time.monotonic() + timeout
        while transitional(state) and time.monotonic() < deadline:
            if self.stop_event.wait(POLL_INTERVAL): raise InterruptedError('已停止')
            state = api.request('/api/v1/singleplayer?format=json')
        return state

    def decide_routed(self, state):
        """Pick the model; in auto mode escalate to the next tier when one cannot decide."""
        selection = models.settings()
        self.want_strategy = False
        if not selection['model_key'].startswith('auto:'):
            return self.decide(state, selection)
        mode = selection['model_key'].split(':', 1)[1]
        fight = fight_key(state)
        self.want_strategy = mode == 'brief' and fight is not None and (self.strategy or {}).get('fight') != fight
        strong = hard_screen(state) or self.want_strategy or (mode == 'combat' and fight is not None)
        tiers = models.AUTO_TIERS[1 if strong else 0:]
        for tier, stronger in zip(tiers, tiers[1:] + (None,)):
            try:
                decision = self.decide(state, {**selection, 'model_key': 'claude:' + tier})
                reason = '沒有給出可執行的動作' if undecided(state, decision) else None
            except ValueError as exc:  # Malformed answer; login, quota or network errors are not retried.
                if stronger is None: raise
                reason = str(exc)
            if reason is None or stronger is None:
                if self.want_strategy and decision.get('strategy'):
                    self.strategy = {'fight': fight, 'text': decision['strategy']}
                decision['advice'] += f'\n（{tier.title()} 判斷）'
                return decision  # The top tier's answer stands even if it gives up, so takeover stops.
            self.report(f'{tier.title()} {reason}，改由 {stronger.title()} 判斷……')

    def decide(self, state, selection=None):
        prune_runs()
        run_dir = ROOT / 'button-runtime' / str(uuid.uuid4())
        run_dir.mkdir(parents=True)
        schema_path = run_dir / 'schema.json'
        answer_path = run_dir / 'answer.json'
        schema_path.write_text(json.dumps(SCHEMA), encoding='utf-8')
        compact = api.compact(state)
        # Deck identities are essential for decisions; pile order is not needed.
        if 'player' in state and 'deck' in state['player']:
            compact['player']['deck'] = deck_summary(state['player']['deck'])
        if state.get('state_type') == 'card_reward': compact['card_advice'] = advise(state)
        if state.get('state_type') == 'shop': compact['shop_card_advice'] = advise(state)
        if state.get('state_type') == 'map':
            compact['route_facts'] = route_facts(state)
            # The candidate routes already carry every room and coordinate; the raw ~60-node graph
            # doubled the map prompt (about 4.6k characters).
            compact.get('map', {}).pop('nodes', None)
        if state.get('state_type') == 'rest_site': compact['rest_facts'] = rest_facts(state, self.route)
        if state.get('state_type') == 'event':
            event = state.get('event') or {}
            advice = event_advice(event.get('event_id'), event.get('options') or [],
                                  (state.get('player') or {}).get('character_id') or '')
            if advice: compact['event_advice'] = advice
            ahead = route_ahead(self.route)
            if ahead: compact['route_ahead'] = ahead
        if state.get('state_type') in ('card_reward', 'shop', 'card_select', 'event'):
            compact['deck_size'] = len((state.get('player') or {}).get('deck') or [])
            compact['deck_profile'] = deck_profile((state.get('player') or {}).get('deck') or [], (state.get('run') or {}).get('act'))
        if self.want_strategy: compact['request_strategy'] = True
        elif self.strategy and self.strategy['fight'] == fight_key(state): compact['fight_strategy'] = self.strategy['text']
        if state.get('state_type') in COMBAT:
            cached = self.local_search
            result = cached[1] if cached and cached[0] == fingerprint(state) else None
            candidates = turn_candidates(state, result)
            if candidates: compact['turn_candidates'] = candidates
            compact['combat_facts'] = combat_facts(state)
            piles = pile_contents(state.get('player') or {})
            if piles: compact['pile_contents'] = piles
            notes = enemy_notes(state)
            if notes: compact['enemy_notes'] = notes
            ascension = (state.get('run') or {}).get('ascension') or 0
            intel = {enemy: monster_intel(enemy, ascension) for enemy in enemy_ids(state)}
            if any(intel.values()): compact['enemy_intel'] = {k: v for k, v in intel.items() if v}
        # Only this screen's actions: the full list let the model pick look-alikes from other screens.
        compact['keyword_glossary'] = pull_keywords(compact)
        actions = {a: sorted(FIELDS[a]) for a in sorted(allowed_actions(state))}
        prompt = (instructions() + '\n目前畫面可用動作與參數：' + json.dumps(actions, ensure_ascii=False)
                  + '\n遊戲狀態：' + json.dumps(compact, ensure_ascii=False))
        command,provider=models.build_command(selection or models.settings(),run_dir,SCHEMA)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with self.lock:
            if self.stop_event.is_set(): raise InterruptedError('已停止')
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                            errors='replace', creationflags=flags,cwd=run_dir)
            process = self.process
        try:
            out, err = process.communicate(prompt, timeout=180)
        except subprocess.TimeoutExpired:
            process.kill(); process.communicate()
            raise RuntimeError('模型回覆逾時，請稍後重試')
        finally:
            with self.lock: self.process = None
        if self.stop_event.is_set(): raise InterruptedError('已停止')
        (run_dir / 'codex.log').write_text(out + '\n' + err,encoding='utf-8')
        if process.returncode != 0:
            raise RuntimeError(provider+' 呼叫失敗：' + (err or out)[-700:])
        return models.parse_answer(provider,out,answer_path)

    def run(self, takeover=False, max_steps=30):
        opened_cards = set()
        plan = None
        try:
            for step in range(max_steps if takeover else 1):
                if self.stop_event.is_set(): break
                state = api.request('/api/v1/singleplayer?format=json')
                # Settle first: a room that is still loading would skip the play-phase wait, and a fight
                # that just began has no hand yet.
                if takeover: state = self.wait_until_settled(self.wait_for_play_phase(self.wait_until_settled(state)))
                if state.get('state_type') in {'menu','game_over','unknown','overlay'}:
                    self.report('先踏進尖塔，我才有施展的餘地。\n請先進入遊戲，或完成目前需要手動處理的畫面。'); break
                self.report('我有一份提案……容我看看你的牌。' if not takeover else f'把控制權交給我。你會喜歡結果的。\n接手中：第 {step+1} 步／{max_steps} 步')
                decision = auto_decision(state, opened_cards) if takeover else None
                self.local_search = None
                if takeover and decision is None and state.get('state_type') in COMBAT:
                    result = search_turn(state, include_candidates=True)
                    self.local_search = (fingerprint(state), result)
                    decision = result['decision']
                    if decision is not None: plan = None  # Re-read and search after each executed move.
                if takeover and decision is None:
                    decision = route_decision(self.route, state) or plan_decision(plan, state)
                if decision is None:
                    if not self.allow_model:
                        self.report('程式無法可靠處理目前局面，已停止接手。\n此模式不會呼叫 AI，請手動選擇。')
                        break
                    decision = self.decide_routed(state)
                    if state.get('state_type') == 'map': self.route = start_route(state, decision)
                    # Buying card removal opens a selection screen; keep the shopping list for afterwards.
                    keep = plan and plan['kind'] == 'shop' and state.get('state_type') == 'card_select'
                    plan = start_plan(state, decision) or (plan if keep else None)
                    first = first_card(state, decision)
                    if first and wastes_energy_card(state, first) and plan and plan['kind'] == 'turn':
                        plan['expected'][first.get('name')] += 1  # It stays in hand instead of being played.
                        decision = plan_decision(plan, state) or decision  # Skip the wasted energy card.
                elif plan and not plan['queue']: plan = None
                self.report(decision['advice'])
                if not takeover or decision['stop'] or decision['action'] == 'none': break
                body = validate(state, decision['action'], json.loads(decision['parameters_json']))
                current = api.request('/api/v1/singleplayer?format=json')
                if not still_valid(state, current, body):
                    self.report('看來你另有打算。控制權歸你了。\n局面已改變，已停止接手。'); break
                with self.lock:
                    if self.stop_event.is_set(): break
                    result = api.request('/api/v1/singleplayer', body)
                if result.get('status') != 'ok': raise RuntimeError(str(result))
                self.report(decision['advice'] + '\n〔已執行〕')
                self.wait_for_change(current)
            else:
                self.report(f'這份契約，暫且履行到此。\n已完成 {max_steps} 步並停止；需要時可再次呼叫。')
        except InterruptedError:
            self.report('也好，讓我看看你的本事。\n已停止接手，控制權歸你。')
        except Exception as exc:
            self.report('這份交易遇到了些阻礙。\n已停止：' + str(exc))
        finally:
            if self.stop_event.is_set(): self.report('也好，讓我看看你的本事。\n已停止接手，控制權歸你。')
            self.report('__DONE__')

def bridge_server(port):
    guard=threading.Lock()
    status={'busy':False,'mode':'idle','message':'又來尋求力量了？很好，我有一份提案。\n選擇「只給建議」或「接手遊玩」。','sequence':0}
    def report(message):
        with guard:
            if message=='__DONE__': status.update(busy=False,mode='idle')
            else: status['message']=message
            status['sequence']+=1
    controller=Controller(report)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def respond(self,value,code=200):
            body=json.dumps(value,ensure_ascii=False).encode('utf-8')
            self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            url=urlsplit(self.path)
            if url.path=='/quota':
                key=parse_qs(url.query).get('model',[models.settings()['model_key']])[0]
                if key not in {m['key'] for m in models.MODELS}: self.respond({'error':'unknown model'},400); return
                self.respond(spire_quota.quota(key)); return
            if self.path=='/claude-auth':
                self.respond(models.claude_auth_status()); return
            if self.path=='/models':
                self.respond({'models':models.MODELS,'selection':models.settings()}); return
            if self.path!='/status': self.respond({'error':'unknown endpoint'},404); return
            with guard: snapshot=dict(status)
            self.respond(snapshot)
        def do_POST(self):
            # No CORS support; JSON-only requests cannot be sent cross-origin by webpages.
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                self.respond({'error':'JSON required'},415); return
            length=int(self.headers.get('Content-Length','0'))
            if length>1024: self.respond({'error':'request too large'},413); return
            raw=self.rfile.read(length)
            if self.path=='/settings':
                try:
                    with guard:
                        if status['busy']: self.respond({'error':'busy'},409); return
                        result=models.save_settings(json.loads(raw))
                    self.respond(result)
                except (ValueError,KeyError) as exc: self.respond({'error':str(exc)},400)
                return
            if self.path=='/login-claude':
                try:
                    executable=models.find_cli('claude')
                    launcher=ROOT/'登入Claude.cmd'
                    launcher.write_text('@echo off\r\n"'+executable+'" auth login --claudeai\r\npause\r\n',encoding='utf-8')
                    subprocess.Popen(['cmd.exe','/c',str(launcher)],creationflags=subprocess.CREATE_NEW_CONSOLE)
                    self.respond({'status':'ok'})
                except Exception as exc: self.respond({'error':str(exc)},500)
                return
            if self.path=='/stop':
                controller.stop(); report('這麼快就想收回控制權？\n正在停止，稍候便交還給你。'); self.respond({'status':'ok'}); return
            if self.path=='/shutdown':
                controller.stop(); self.respond({'status':'ok'})
                threading.Thread(target=self.server.shutdown,daemon=True).start(); return
            if self.path not in ('/advice','/takeover'):
                self.respond({'error':'unknown endpoint'},404); return
            with guard:
                if status['busy']: self.respond({'error':'busy'},409); return
                status.update(busy=True,mode=self.path[1:],message='讓我看看，你又把自己帶進了什麼局面……\n正在讀取遊戲狀態。')
                controller.stop_event.clear()
            threading.Thread(target=controller.run,args=(self.path=='/takeover',),daemon=True).start()
            self.respond({'status':'ok'})
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    try: server.serve_forever()
    finally: controller.stop(); server.server_close()

def main():
    parser = argparse.ArgumentParser(description='Bridge between the in-game Vakuu panel and the model CLIs.')
    parser.add_argument('--bridge-server', type=int, help='Serve the panel on this localhost port')
    parser.add_argument('--check', action='store_true', help='Print the game API health check')
    args = parser.parse_args()
    if args.bridge_server:
        bridge_server(args.bridge_server)
    elif args.check:
        print(json.dumps(api.request('/'), ensure_ascii=False))
    else:
        parser.print_help()

if __name__=='__main__': main()
