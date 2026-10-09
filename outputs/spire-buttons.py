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

def validate(state, action, params):
    allowed = ALLOWED.get(state.get('state_type'), set()) | {'use_potion', 'discard_potion'}
    if action not in allowed or action not in FIELDS:
        raise ValueError('不允許在目前畫面執行這個操作：' + action)
    if not isinstance(params, dict) or set(params) - FIELDS[action]:
        raise ValueError('操作參數不符合遊戲 API')
    required = FIELDS[action] - {'target'}
    if not required.issubset(params):
        raise ValueError('操作缺少必要參數')
    for key, value in params.items():
        if key == 'target':
            if not isinstance(value, str): raise ValueError('目標 ID 格式錯誤')
        elif type(value) is not int or value < 0:
            raise ValueError('卡牌或選項索引格式錯誤')
    return {'action': action, **params}

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

    def decide(self, state):
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
            '卡牌 index 會隨出牌改變，只決定當前的一個動作。target 使用敵人 entity_id。'
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
        try:
            for step in range(max_steps if takeover else 1):
                if self.stop_event.is_set(): break
                state = api.request('/api/v1/singleplayer?format=json')
                if state.get('state_type') in {'menu','game_over','unknown','overlay'}:
                    self.report('先踏進尖塔，我才有施展的餘地。\n請先進入遊戲，或完成目前需要手動處理的畫面。'); break
                self.report('我有一份提案……容我看看你的牌。' if not takeover else f'把控制權交給我。你會喜歡結果的。\n接手中：第 {step+1} 步／{max_steps} 步')
                rating = advise(state) if state.get('state_type') == 'card_reward' else None
                rows = rating.get('ranked_cards', []) if rating else []
                if takeover and rating and rating.get('status') == 'ok' and len(rows) >= 2 and rows[0]['score'] - rows[1]['score'] > 5:
                    decision = {'advice':f"這份力量，倒還值得收下。\n建議：選 {rows[0]['name']}（{rows[0]['score']} 分，依 BoberInSpire 評分）。", 'stop':False,
                                'action':'select_card_reward', 'parameters_json':json.dumps({'card_index':rows[0]['index']})}
                else:
                    decision = self.decide(state)
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
