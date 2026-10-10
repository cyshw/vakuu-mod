"""Read subscription limits without requesting model inference."""
import json
import os
import queue
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from spire_models import find_cli, uses_claude

_lock=threading.Lock()
_cache={}

def format_limits(result):
    buckets=result.get('rateLimitsByLimitId')
    if not buckets:
        legacy=result.get('rateLimits')
        buckets={'codex':legacy} if legacy else {}
    lines=[]
    for key,bucket in buckets.items():
        if not isinstance(bucket,dict): continue
        for window in ('primary','secondary'):
            item=bucket.get(window)
            if not isinstance(item,dict): continue
            used=item.get('usedPercent')
            if type(used) not in (int,float): continue
            minutes=item.get('windowDurationMins')
            duration=f'{minutes/1440:g} 天' if isinstance(minutes,(int,float)) and minutes>=1440 else f'{minutes/60:g} 小時' if isinstance(minutes,(int,float)) and minutes>=60 else f'{minutes} 分鐘' if minutes is not None else '用量窗口'
            reset=item.get('resetsAt')
            stamp=datetime.fromtimestamp(reset,timezone(timedelta(hours=8))).strftime('%m/%d %H:%M') if isinstance(reset,(int,float)) else '未知'
            label=bucket.get('limitName') or key
            lines.append(f'{label} · {duration}：剩餘 {max(0,min(100,100-used)):g}%\n重置：{stamp}（台灣時間）')
    return '\n'.join(lines) if lines else '目前無法取得 Codex 剩餘額度。'

def read_codex_limits():
    process=subprocess.Popen([find_cli('codex'),'app-server'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,text=True,encoding='utf-8',errors='replace',
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    messages=queue.Queue()
    def read():
        for line in process.stdout:
            try: messages.put(json.loads(line))
            except ValueError: pass
        messages.put(None)
    threading.Thread(target=read,daemon=True).start()
    def send(value):
        process.stdin.write(json.dumps(value)+'\n'); process.stdin.flush()
    def response(identifier):
        deadline=time.monotonic()+15
        while True:
            value=messages.get(timeout=max(0.01,deadline-time.monotonic()))
            if value is None: raise RuntimeError('額度服務已結束')
            if value.get('id')==identifier:
                if 'error' in value: raise RuntimeError('額度查詢不可用')
                return value['result']
            if time.monotonic()>=deadline: raise TimeoutError('額度查詢逾時')
    try:
        send({'method':'initialize','id':1,'params':{'clientInfo':{'name':'vakuu_quota','version':'0.1.0'}}})
        response(1)
        send({'method':'initialized','params':{}})
        send({'method':'account/rateLimits/read','id':2})
        return response(2)
    finally:
        process.stdin.close()
        if process.poll() is None: process.terminate()
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        process.stdout.close()

CLAUDE_WINDOWS=(('five_hour','5 小時'),('seven_day','7 天'),('seven_day_opus','7 天 · Opus'),('seven_day_sonnet','7 天 · Sonnet'))

def format_claude_limits(result):
    lines=[]
    for key,label in CLAUDE_WINDOWS:
        item=result.get(key)
        if not isinstance(item,dict): continue
        used=item.get('utilization')
        if type(used) not in (int,float): continue
        try: stamp=datetime.fromisoformat(item['resets_at']).astimezone(timezone(timedelta(hours=8))).strftime('%m/%d %H:%M')
        except (KeyError,TypeError,ValueError): stamp='未知'
        lines.append(f'Claude · {label}：剩餘 {max(0,min(100,100-used)):g}%\n重置：{stamp}（台灣時間）')
    return '\n'.join(lines) if lines else '目前無法取得 Claude 剩餘額度。'

def read_claude_limits():
    # Same read-only endpoint Claude Code's /usage uses; never refreshes the token so the CLI keeps owning it.
    oauth=json.loads((Path.home()/'.claude/.credentials.json').read_text(encoding='utf-8'))['claudeAiOauth']
    if oauth['expiresAt']/1000<time.time(): raise RuntimeError('登入憑證已過期')
    request=urllib.request.Request('https://api.anthropic.com/api/oauth/usage',headers={
        'Authorization':'Bearer '+oauth['accessToken'],'anthropic-beta':'oauth-2025-04-20','User-Agent':'vakuu-quota'})
    with urllib.request.urlopen(request,timeout=15) as response: return json.load(response)

def read_limits(provider):
    if provider=='claude':
        try: return format_claude_limits(read_claude_limits())
        except RuntimeError: return 'Claude 登入憑證已過期，完成一次 Claude 呼叫後再展開面板。'
        except (OSError,ValueError,KeyError,TypeError): return '目前無法取得 Claude 剩餘額度，稍後再展開面板重試。'
    try: return format_limits(read_codex_limits())
    except (OSError,RuntimeError,ValueError,KeyError,queue.Empty,TimeoutError): return '目前無法取得 Codex 剩餘額度，稍後再展開面板重試。'

def quota(model_key):
    provider='claude' if uses_claude(model_key) else model_key.split(':',1)[0]
    with _lock:
        cached=_cache.get(provider)
        if cached and time.monotonic()-cached['time']<60: return cached['value']
        result={'message':read_limits(provider),'provider':provider}
        _cache[provider]={'time':time.monotonic(),'value':result}
        return result
