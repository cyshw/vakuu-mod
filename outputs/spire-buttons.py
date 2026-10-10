"""Floating buttons that invoke the installed Codex CLI on live STS2 state."""
import argparse
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import tkinter as tk
from tkinter import ttk
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('game_api', ROOT / 'sts2-control.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
from sts2_advisor import advise
import spire_models as models
import spire_quota

FIELDS = {
    'play_card': {'card_index', 'target'}, 'end_turn': set(),
    'use_potion': {'slot', 'target'}, 'discard_potion': {'slot'},
    'combat_select_card': {'card_index'}, 'combat_confirm_selection': set(),
    'claim_reward': {'index'}, 'proceed': set(),
    'select_card_reward': {'card_index'}, 'skip_card_reward': set(),
    'choose_map_node': {'index'}, 'choose_event_option': {'index'}, 'advance_dialogue': set(),
    'choose_rest_option': {'index'}, 'shop_purchase': {'index'},
    'claim_treasure_relic': {'index'}, 'select_card': {'index'},
    'confirm_selection': set(), 'cancel_selection': set(),
    'select_bundle': {'index'}, 'confirm_bundle_selection': set(), 'cancel_bundle_selection': set(),
    'select_relic': {'index'}, 'skip_relic_selection': set(),
}
ALLOWED = {
    'monster': {'play_card', 'end_turn'}, 'elite': {'play_card', 'end_turn'},
    'boss': {'play_card', 'end_turn'},
    'hand_select': {'combat_select_card', 'combat_confirm_selection'},
    'rewards': {'claim_reward', 'proceed'},
    'card_reward': {'select_card_reward', 'skip_card_reward'},
    'map': {'choose_map_node'}, 'event': {'choose_event_option', 'advance_dialogue'},
    'rest_site': {'choose_rest_option', 'proceed'},
    'shop': {'shop_purchase', 'proceed'}, 'fake_merchant': {'shop_purchase', 'proceed'},
    'treasure': {'claim_treasure_relic', 'proceed'},
    'card_select': {'select_card', 'confirm_selection', 'cancel_selection'},
    'bundle_select': {'select_bundle', 'confirm_bundle_selection', 'cancel_bundle_selection'},
    'relic_select': {'select_relic', 'skip_relic_selection'},
}
SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'advice': {'type': 'string'}, 'stop': {'type': 'boolean'},
        'action': {'type': 'string', 'enum': ['none'] + list(FIELDS)},
        'parameters_json': {'type': 'string'},
    }, 'required': ['advice', 'stop', 'action', 'parameters_json']
}

def fingerprint(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def selection_complete(state):
    # select_card toggles a card; once the preview opens the choice is made and only confirm/cancel are safe.
    select = state.get('card_select') or {}
    return state.get('state_type') == 'card_select' and bool(select.get('preview_showing'))

def validate(state, action, params):
    allowed = ALLOWED.get(state.get('state_type'), set()) | {'use_potion', 'discard_potion'}
    if selection_complete(state): allowed = {'confirm_selection', 'cancel_selection'}
    if action not in allowed or action not in FIELDS:
        raise ValueError('不允許在目前畫面執行這個操作：' + action)
    if not isinstance(params, dict) or set(params) - FIELDS[action]:
        raise ValueError('操作參數不符合遊戲 API')
    # An optional null target means no target (e.g. Block cards), not an entity ID.
    params = {key:value for key,value in params.items() if not (key == 'target' and value is None)}
    required = FIELDS[action] - {'target'}
    if not required.issubset(params):
        raise ValueError('操作缺少必要參數')
    for key, value in params.items():
        if key == 'target':
            if not isinstance(value, str): raise ValueError('目標 ID 格式錯誤')
        elif type(value) is not int or value < 0:
            raise ValueError('卡牌或選項索引格式錯誤')
    return {'action': action, **params}

def prune_runs(keep=40):
    # Each call leaves a schema/log folder; keep only recent ones for debugging.
    runs = sorted((p for p in (ROOT / 'button-runtime').glob('*-*-*-*-*') if p.is_dir()),
                  key=lambda p: p.stat().st_mtime)
    for old in runs[:-keep]: shutil.rmtree(old, ignore_errors=True)

def transitional(state):
    """Screens caught mid-animation: nothing to choose yet, so asking the model only makes it give up."""
    kind = state.get('state_type')
    screen = state.get(kind) or {}
    if kind in ('rest_site', 'rewards'):
        return not screen.get('options' if kind == 'rest_site' else 'items') and not screen.get('can_proceed')
    if kind == 'treasure':  # "Opening chest..." / "Treasure room loading..." carry no relic list yet,
        return 'message' in screen and 'relics' not in screen  # even though Skip is already enabled.
    if kind in ('monster', 'elite', 'boss'):  # "Combat ended. Waiting for rewards..."
        return 'battle' not in state
    if kind == 'shop':  # Inventory not ready right after entering.
        return 'error' in screen and not screen.get('items')
    return False

def _auto(advice, action, **params):
    return {'advice': advice, 'stop': False, 'action': action, 'parameters_json': json.dumps(params)}

def auto_decision(state, opened=None):
    """Takeover steps with exactly one sensible move; skipping the model saves a full CLI call."""
    kind = state.get('state_type')
    player = state.get('player') or {}
    if selection_complete(state) and state['card_select'].get('can_confirm'):
        return _auto('契約已定，落筆為證。\n建議：確認剛才的選擇。', 'confirm_selection')
    if kind == 'card_reward':
        rating = advise(state)
        rows = rating.get('ranked_cards', [])
        if rating.get('status') == 'ok' and len(rows) >= 2 and rows[0]['score'] - rows[1]['score'] > 5:
            return _auto(f"這份力量，倒還值得收下。\n建議：選 {rows[0]['name']}（{rows[0]['score']} 分，依 BoberInSpire 評分）。",
                         'select_card_reward', card_index=rows[0]['index'])
    if kind == 'rewards':
        rewards = state.get('rewards') or {}
        room = len(player.get('potions') or []) < player.get('max_potion_slots', 0)
        for item in rewards.get('items') or []:
            if item.get('type') in ('gold', 'relic') or (item.get('type') == 'potion' and room):
                return _auto('白給的東西，收下便是。\n建議：領取' + (item.get('description') or '獎勵') + '。',
                             'claim_reward', index=item['index'])
        # Opening a card reward commits to nothing; the real choice happens on the card screen.
        # Open each one once, so a skipped reward that stays listed goes to the model instead of looping.
        cards = [item for item in rewards.get('items') or [] if item.get('type') == 'card']
        if cards and opened is not None:
            key = ((state.get('run') or {}).get('floor'), len(cards))
            if key not in opened:
                opened.add(key)
                return _auto('看看命運端上了什麼。\n建議：打開卡牌獎勵。', 'claim_reward', index=cards[0]['index'])
        if not rewards.get('items') and rewards.get('can_proceed'):
            return _auto('戰利品已盡，繼續前行。\n建議：離開獎勵畫面。', 'proceed')
    if kind == 'rest_site':
        rest = state.get('rest_site') or {}
        if rest.get('can_proceed') and not any(o.get('is_enabled', True) for o in rest.get('options') or []):
            return _auto('篝火已盡其用，該上路了。\n建議：離開休息點。', 'proceed')
    if kind == 'treasure':
        treasure = state.get('treasure') or {}
        relics = treasure.get('relics')
        if isinstance(relics, list) and len(relics) == 1:
            return _auto('寶箱裡的東西，自然歸你。\n建議：拿取' + (relics[0].get('name') or '遺物') + '。',
                         'claim_treasure_relic', index=relics[0]['index'])
        if not relics and 'message' not in treasure and treasure.get('can_proceed'):
            return _auto('箱子空了，繼續前行。\n建議：離開寶箱房。', 'proceed')
    if kind == 'map':
        options = (state.get('map') or {}).get('next_options') or []
        if len(options) == 1:
            return _auto('只有一條路，那就走吧。\n建議：前往唯一可選的地點。', 'choose_map_node', index=options[0]['index'])
    if kind == 'event':
        event = state.get('event') or {}
        if event.get('in_dialogue'):
            return _auto('讓他說完。\n建議：繼續對話。', 'advance_dialogue')
        options = [o for o in event.get('options') or [] if not o.get('is_locked')]
        if len(options) == 1:
            return _auto('別無選擇，倒也省事。\n建議：選擇唯一可用的選項。', 'choose_event_option', index=options[0]['index'])
    if kind in ('monster', 'elite', 'boss'):
        hand = player.get('hand')
        potions = [p for p in player.get('potions') or [] if p.get('can_use_in_combat')]
        if isinstance(hand, list) and not any(card.get('can_play') for card in hand) and not potions:
            return _auto('手上已無可出之牌。\n建議：結束回合。', 'end_turn')
    return None

class Controller:
    def __init__(self, report):
        self.report = report
        self.stop_event = threading.Event()
        self.process = None
        self.lock = threading.Lock()

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
            if self.stop_event.wait(0.5): raise InterruptedError('已停止')
            state = api.request('/api/v1/singleplayer?format=json')
        return state

    def wait_until_settled(self, state, timeout=10):
        # Right after resting or claiming the last reward, the screen has nothing to choose and the
        # proceed button is still animating in; asking the model then makes it give up.
        deadline = time.monotonic() + timeout
        while transitional(state) and time.monotonic() < deadline:
            if self.stop_event.wait(0.5): raise InterruptedError('已停止')
            state = api.request('/api/v1/singleplayer?format=json')
        return state

    def decide(self, state):
        prune_runs()
        run_dir = ROOT / 'button-runtime' / str(uuid.uuid4())
        run_dir.mkdir(parents=True)
        schema_path = run_dir / 'schema.json'
        answer_path = run_dir / 'answer.json'
        schema_path.write_text(json.dumps(SCHEMA), encoding='utf-8')
        compact = api.compact(state)
        # Deck identities are essential for decisions; pile order is not needed.
        if 'player' in state and 'deck' in state['player']:
            compact['player']['deck'] = [{k:c.get(k) for k in ('id','name','is_upgraded','cost')} for c in state['player']['deck']]
        if state.get('state_type') == 'card_reward': compact['card_advice'] = advise(state)
        prompt = (
            '你扮演殺戮尖塔2的瓦庫 Vakuu「第一個惡魔」，同時是遊戲顧問。'
            '瓦庫以力量、交易和契約誘惑對方，帶著優越感與冷淡的戲謔；不是熱心旅伴，也不是網路嘴砲。'
            'advice 使用繁體中文：先一句不超過25字的原創瓦庫低語，再換行「建議：」說明具體下一步和戰術理由。'
            '角色語氣只影響文字，不影響決策；以玩家存活與勝利為目標，不故意亂打，不虛構代價或遊戲效果。'
            '可依 character_id 調整稱呼：故障機器人為構裝體，鐵甲戰士為戰士，儲君為殿下；未知角色不要亂猜。'
            '不要編造角色背景、見面次數或遊戲外記憶，不堆疊笑聲，不長篇演戲。'
            '總共最多3句，戰術理由必須清楚；危急時先給關鍵操作。'
            '只根據下列 JSON 決策，不呼叫工具，不讀寫檔案，不操作電腦。'
            '輸出一個遊戲 API 動作。parameters_json 是參數物件的 JSON 字串。'
            '卡牌 index 會隨出牌改變，只決定當前的一個動作。target 使用敵人 entity_id 字串。'
            'card_select 畫面的 select_card 是切換選取，同一張再選會取消；preview_showing 為 true 代表已選好，'
            '只能 confirm_selection 確認（或 cancel_selection 重選），不要再選牌。'
            '商店沒有想買或買不起的商品時用 proceed 離開；proceed 會自動先關閉商品欄。'
            '防禦、自身效果、全體攻擊等不需單一目標的卡牌，省略 target 欄位，不要傳 null 或數字。'
            '抓牌採用 BoberInSpire 評分；前兩名分差5分以內，依牌組、遺物、血量、頭目判斷並考慮跳過；'
            '資料未知或不完整時自行判斷。分數是規則分數，不是勝率。'
            '優先減少失血、完成目前遊戲。不要放棄遊戲或重開。若無有效動作、在主選單、或需要人工協助，'
            '輸出 action=none、stop=true。你看不到這個按鈕以外的聊天。'
            '可用動作與參數：' + json.dumps(FIELDS, default=list) + '\n遊戲狀態：' + json.dumps(compact,ensure_ascii=False)
        )
        selection=models.settings()
        command,provider=models.build_command(selection,run_dir,SCHEMA)
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
        try:
            for step in range(max_steps if takeover else 1):
                if self.stop_event.is_set(): break
                state = api.request('/api/v1/singleplayer?format=json')
                if takeover: state = self.wait_until_settled(self.wait_for_play_phase(state))
                if state.get('state_type') in {'menu','game_over','unknown','overlay'}:
                    self.report('先踏進尖塔，我才有施展的餘地。\n請先進入遊戲，或完成目前需要手動處理的畫面。'); break
                self.report('我有一份提案……容我看看你的牌。' if not takeover else f'把控制權交給我。你會喜歡結果的。\n接手中：第 {step+1} 步／{max_steps} 步')
                decision = (auto_decision(state, opened_cards) if takeover else None) or self.decide(state)
                self.report(decision['advice'])
                if not takeover or decision['stop'] or decision['action'] == 'none': break
                body = validate(state, decision['action'], json.loads(decision['parameters_json']))
                current = api.request('/api/v1/singleplayer?format=json')
                if fingerprint(state) != fingerprint(current):
                    self.report('看來你另有打算。控制權歸你了。\n局面已改變，已停止接手。'); break
                with self.lock:
                    if self.stop_event.is_set(): break
                    result = api.request('/api/v1/singleplayer', body)
                if result.get('status') != 'ok': raise RuntimeError(str(result))
                self.report(decision['advice'] + '\n〔已執行〕')
                self.stop_event.wait(1.2)
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--test-advice', action='store_true')
    parser.add_argument('--bridge-server',type=int)
    args = parser.parse_args()
    if args.bridge_server:
        bridge_server(args.bridge_server); return
    if args.check:
        print(json.dumps(api.request('/'), ensure_ascii=False)); return
    if args.test_advice:
        state = api.request('/api/v1/singleplayer?format=json')
        print(json.dumps(Controller(print).decide(state), ensure_ascii=False)); return
    window = tk.Tk()
    window.title('尖塔 · Codex')
    window.geometry('430x320+40+120')
    window.attributes('-topmost', True)
    events = queue.Queue()
    controller = Controller(events.put)
    ttk.Label(window, text='需要時再呼叫 Codex', font=('Microsoft JhengHei',13)).pack(pady=(12,4))
    ttk.Label(window, text='本機 Codex 回覆 · 仍會使用帳號額度').pack()
    bar = ttk.Frame(window); bar.pack(pady=12)
    busy = [False]
    def start(takeover):
        if busy[0]: return
        busy[0]=True
        controller.stop_event.clear()
        for button in buttons: button.config(state='disabled')
        threading.Thread(target=controller.run,args=(takeover,),daemon=True).start()
    buttons = [ttk.Button(bar,text='只給建議',command=lambda:start(False)),
               ttk.Button(bar,text='接手遊玩',command=lambda:start(True))]
    for button in buttons: button.pack(side='left',padx=5)
    ttk.Button(bar,text='停止',command=controller.stop).pack(side='left',padx=5)
    text=tk.Text(window,wrap='word',font=('Microsoft JhengHei',10),height=9)
    text.pack(fill='both',expand=True,padx=12,pady=(0,8))
    text.insert('end','只給建議：讀取目前局面，顯示下一步。\n接手遊玩：每次最多30步，可隨時停止。\n接手期間請暫停手動操作。\n')
    def poll():
        while not events.empty():
            message=events.get()
            if message=='__DONE__':
                busy[0]=False
                for button in buttons: button.config(state='normal')
            else:
                text.insert('end',message+'\n'); text.see('end')
        window.after(150,poll)
    def close():
        controller.stop(); window.destroy()
    window.protocol('WM_DELETE_WINDOW',close)
    poll(); window.mainloop()

if __name__=='__main__': main()
