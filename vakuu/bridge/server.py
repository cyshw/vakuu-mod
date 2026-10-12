"""The HTTP bridge used by the in-game panel."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import argparse
import json
import subprocess
import threading

from core import game_api as api
from core.paths import RUNTIME
from core.screens import in_fight, screen_label
from models import catalog as models
from models import quota
from takeover.controller import Controller

def bridge_server(port):
    guard=threading.Lock()
    status={'busy':False,'mode':'idle','message':'又來尋求力量了？很好，我有一份提案。\n「幫我選」只處理眼前這一步，也能接手這場戰鬥或整局。','sequence':0}
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
                self.respond(quota.quota(key)); return
            if self.path=='/claude-auth':
                self.respond(models.claude_auth_status()); return
            if self.path=='/providers':
                self.respond(models.provider_status()); return
            if self.path=='/models':
                self.respond({'models':models.MODELS,'selection':models.settings()}); return
            if self.path!='/status': self.respond({'error':'unknown endpoint'},404); return
            with guard: snapshot=dict(status)
            try:  # The panel names its "help me choose" button after the screen.
                state=api.request('/api/v1/singleplayer?format=json')
                snapshot.update(screen=state.get('state_type'),screen_label=screen_label(state),in_fight=in_fight(state))
            except Exception: snapshot.update(screen=None,screen_label='幫我選',in_fight=False)
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
            if self.path in ('/login-claude','/login-codex','/login-agy'):
                # Each player signs in to their own subscription in the tool's official flow.
                provider=self.path.split('-',1)[1]
                try:
                    executable=models.find_cli(provider)
                    args={'claude':'auth login --claudeai','codex':'login','agy':''}[provider]
                    launcher=RUNTIME/f'login-{provider}.cmd'
                    launcher.write_text('@echo off\r\nchcp 65001 >nul\r\n"'+executable+'" '+args+'\r\npause\r\n',encoding='utf-8')
                    subprocess.Popen(['cmd.exe','/c',str(launcher)],creationflags=subprocess.CREATE_NEW_CONSOLE)
                    models.forget_provider_status()
                    self.respond({'status':'ok'})
                except Exception as exc: self.respond({'error':str(exc)},500)
                return
            if self.path=='/stop':
                controller.stop(); report('這麼快就想收回控制權？\n正在停止，稍候便交還給你。'); self.respond({'status':'ok'}); return
            if self.path=='/shutdown':
                controller.stop(); self.respond({'status':'ok'})
                threading.Thread(target=self.server.shutdown,daemon=True).start(); return
            scopes={'/takeover-screen':'screen','/takeover-fight':'fight','/takeover-run':'run'}
            if self.path not in ('/advice','/takeover',*scopes):
                self.respond({'error':'unknown endpoint'},404); return
            with guard:
                if status['busy']: self.respond({'error':'busy'},409); return
                status.update(busy=True,mode=self.path[1:],message='讓我看看，你又把自己帶進了什麼局面……\n正在讀取遊戲狀態。')
                controller.stop_event.clear()
            threading.Thread(target=controller.run,args=(self.path!='/advice',),
                             kwargs={'scope':scopes.get(self.path)},daemon=True).start()
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
