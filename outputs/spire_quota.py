"""Read subscription limits without requesting model inference."""
import json
import os
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone, timedelta
from spire_models import find_cli

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

def quota(model_key):
    provider=model_key.split(':',1)[0]
    if provider=='claude':
        return {'message':'Claude 剩餘額度：請查看官方額度頁面。\n目前背景呼叫無法讀取百分比。','provider':provider}
    with _lock:
        if _cache and time.monotonic()-_cache['time']<60: return _cache['value']
        try: message=format_limits(read_codex_limits())
        except (OSError,RuntimeError,ValueError,KeyError,queue.Empty,TimeoutError): message='目前無法取得 Codex 剩餘額度，稍後再展開面板重試。'
        result={'message':message,'provider':provider}
        _cache.update(time=time.monotonic(),value=result)
        return result
