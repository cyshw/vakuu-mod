"""Manual combat lab. Records game actions without starting an AI controller.

python tools/battle_lab.py serve
python tools/battle_lab.py compare PATH.jsonl --limit 5 --config codex:gpt-6.1-sol@low
"""
import argparse
import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'vakuu' / 'button-runtime' / 'lab'
EXAMPLE = {
    'character': 'IRONCLAD', 'encounter': 'ENTOMANCER_ELITE', 'ascension': 0,
    'act': 1, 'floor': 8, 'act_floor': 8, 'hp': 65, 'max_hp': 80, 'gold': 0,
    'deck': [{'id': 'STRIKE_IRONCLAD', 'upgrades': 0}] * 5
            + [{'id': 'DEFEND_IRONCLAD', 'upgrades': 0}] * 4
            + [{'id': 'BASH', 'upgrades': 1}],
    'relics': ['BURNING_BLOOD'], 'potions': []}


def presets():
    result = [{'name': '基礎牌組（開局功能測試）', 'source': None, 'notes': '僅用於測試開局；不是成形流派。', 'spec': EXAMPLE}]
    for path in sorted((ROOT / 'tools' / 'lab-presets').glob('*.json')):
        result.append(json.loads(path.read_text(encoding='utf-8')))
    return result


def card_catalog():
    cache = OUTPUT.parent / 'spire-codex' / 'lab-card-catalog.json'
    try:
        if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
            return json.loads(cache.read_text(encoding='utf-8'))
        req = urllib.request.Request('https://spire-codex.com/api/cards?lang=zht',
                                     headers={'User-Agent': 'vakuu-mod/0.1 (personal combat lab)'})
        with urllib.request.urlopen(req, timeout=10) as response:
            rows = json.load(response)
        catalog = [{'id': r['id'], 'name': r.get('name', r['id']), 'color': r.get('color')} for r in rows]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
        return catalog
    except (OSError, ValueError, KeyError):
        if cache.exists(): return json.loads(cache.read_text(encoding='utf-8'))
        return []


def request(base, payload=None, after=None):
    url = base.rstrip('/') + '/api/v1/lab'
    if after is not None:
        url += '?after=' + str(after)
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=90) as response:
        result = json.load(response)
    if 'error' in result and result.get('attempt') is None:
        raise ValueError(result['error'])
    return result


class Archive:
    """Persist each event once; attempt changes create a separate file."""
    def __init__(self, folder):
        self.folder = Path(folder)
        self.attempt = None
        self.cursor = -1
        self.path = None

    def consume(self, status):
        attempt = status.get('attempt')
        if not attempt:
            return
        if len(attempt) != 32 or any(c not in '0123456789abcdef' for c in attempt):
            raise ValueError('Invalid attempt ID')
        if attempt != self.attempt:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.attempt, self.cursor = attempt, -1
            self.path = self.folder / f'{attempt}.jsonl'
            # Recover an interrupted recorder without duplicating old events.
            if self.path.exists():
                for line in self.path.read_text(encoding='utf-8').splitlines():
                    self.cursor = max(self.cursor, json.loads(line)['index'])
        for event in status.get('events', []):
            if event.get('attempt') != attempt:
                raise ValueError('Mixed attempts in recorder response')
            index = event['index']
            if index <= self.cursor:
                continue
            if index != self.cursor + 1:
                raise ValueError('Missing events: fetch full attempt before continuing')
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            self.cursor = index


def target_id(state, combat_id):
    if combat_id is None:
        return None
    enemies = (state.get('battle') or {}).get('enemies') or []
    match = next((e for e in enemies if e.get('combat_id') == combat_id), None)
    if match is None:
        raise ValueError('Recorded target missing from snapshot')
    return match['entity_id']


def human_decision(event):
    action, state = event['action'], event['state']
    kind = action['action']
    if kind == 'play_card':
        index = action['card_index']
        hand = (state.get('player') or {}).get('hand') or []
        if not isinstance(index, int) or not 0 <= index < len(hand):
            raise ValueError('Human card missing from snapshot')
        card_id = hand[index].get('id') or hand[index].get('card_id')
        if card_id != action['card_id']:
            raise ValueError('Human card identity differs from snapshot')
        params = {'card_index': index, 'target': target_id(state, action.get('target_combat_id'))}
    elif kind == 'use_potion':
        params = {'slot': action['slot'], 'target': target_id(state, action.get('target_combat_id'))}
    elif kind == 'end_turn':
        params = {}
    else:
        raise ValueError('Unsupported human action')
    return {'action': kind, 'parameters_json': json.dumps(params)}


def same_first_action(left, right):
    if left['action'] != right['action']:
        return False
    a, b = (json.loads(d.get('parameters_json') or '{}') for d in (left, right))
    keys = {'play_card': ('card_index', 'target'), 'use_potion': ('slot', 'target')}.get(left['action'], ())
    return all(a.get(k) == b.get(k) for k in keys)


def compare(path, config, limit):
    # Import only on explicit comparison; recording never initializes a model.
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'exam'))
    from replay_eval import decide
    events = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()]
    finished = {e['action']['before_index'] for e in events
                if e['kind'] == 'after_action' and e['action']['status'].lower() == 'finished'}
    results = []
    for event in events:
        if event['kind'] != 'before_action':
            continue
        if event['index'] not in finished:
            continue
        try:
            human = human_decision(event)
        except ValueError as error:
            results.append({'event': event['index'], 'skipped': str(error)})
            continue
        model = decide(config, event['state'])
        results.append({'event': event['index'], 'state': event['state'], 'human': human,
                        'model': model, 'config': config,
                        'different': not same_first_action(human, model),
                        'approved': False, 'note': 'Difference is not proof of a better move; review before making an exam.'})
        if sum('model' in r for r in results) >= limit:
            break
    output = Path(path).with_suffix('.comparison.json')
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return output


PAGE = '''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>自訂戰鬥練習</title>
<style>body{font:16px system-ui;background:#101820;color:#e9eef2;max-width:960px;margin:30px auto;padding:0 20px}
textarea,input,select{background:#1d2b37;color:#fff;border:1px solid #536878;padding:8px;font:15px system-ui}
textarea{width:96%;height:260px;font:14px monospace}button{margin:8px 8px 8px 0;padding:9px 13px;cursor:pointer}
pre{white-space:pre-wrap}a{color:#83caff}.fields{display:flex;flex-wrap:wrap;gap:12px}.fields label{display:flex;flex-direction:column;gap:5px}
input[type=number]{width:80px}table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid #344854;padding:5px}h2{margin-top:25px}
#operation{color:#ffce78}#note{line-height:1.6}.resource{width:95%}</style>
<h1>自訂戰鬥練習</h1><p>在遊戲畫面手動出牌。重打沿用配置並重新隨機化；錄製不呼叫 AI。<br>
首次開局先回到主選單，並停止 AI 接手控制器。</p>
<label>牌組範例 <select id="preset" onchange="loadPreset()"></select></label><p id="note"></p>
<div class="fields" id="fields"></div>
<h2>牌組 <span id="count"></span></h2>
<input id="card" list="cards" placeholder="卡牌名稱或 ID"><datalist id="cards"></datalist>
<label>張數 <input id="copies" type="number" value="1" min="1" max="20"></label>
<label>升級 <input id="upgrade" type="number" value="0" min="0" max="10"></label>
<button onclick="addCard()">加入牌組</button><table><thead><tr><th>卡牌</th><th>升級次數</th><th></th></tr></thead><tbody id="deck"></tbody></table>
<h2>遺物與藥水</h2><p>以逗號分隔 ID，留空即清空。只支援下方列出的遺物。</p>
<label>遺物 <input class="resource" id="relicInput" onchange="saveResources()"></label><br>
<label>藥水 <input class="resource" id="potionInput" onchange="saveResources()"></label>
<p>支援遺物：<span id="relics">等待連線</span></p>
<button onclick="send('start')">套用配置並開始戰鬥</button><button onclick="send('retry')">同配置再打一場</button>
<button onclick="send('stop')">停止錄製</button><p id="operation"></p>
<details><summary>進階 JSON 編輯</summary><textarea id="spec"></textarea><br><button onclick="readJson()">從 JSON 更新表單</button></details>
<h2>錄製狀態</h2><pre id="status"></pre>
<script>
const el=id=>document.getElementById(id);let profiles=[],config={},catalog=[];
const fields=[['character','角色','text'],['encounter','遭遇 ID','text'],['ascension','進階','number'],['act','幕','number'],
['floor','全局樓層','number'],['act_floor','本幕樓層','number'],['hp','目前 HP','number'],['max_hp','最大 HP','number'],['gold','金幣','number']];
for(const [key,label,type] of fields){const node=document.createElement('label');node.textContent=label;const input=document.createElement('input');
input.type=type;input.id='field-'+key;input.onchange=()=>{config[key]=type==='number'?Number(input.value):input.value.trim().toUpperCase();sync()};node.append(input);el('fields').append(node)}
function sync(){el('spec').value=JSON.stringify(config,null,2);el('count').textContent='（'+config.deck.length+' 張）'}
function render(){for(const [k] of fields)el('field-'+k).value=config[k];el('relicInput').value=config.relics.join(', ');
el('potionInput').value=config.potions.join(', ');renderDeck();sync()}
function renderDeck(){el('deck').replaceChildren();config.deck.forEach((card,index)=>{const row=document.createElement('tr'),name=document.createElement('td');
const found=catalog.find(x=>x.id===card.id);name.textContent=(found?found.name:card.id)+' · '+card.id;const up=document.createElement('td'),input=document.createElement('input');
input.type='number';input.min=0;input.value=card.upgrades;input.onchange=()=>{card.upgrades=Number(input.value);sync()};up.append(input);
const remove=document.createElement('td'),button=document.createElement('button');button.textContent='移除';button.onclick=()=>{config.deck.splice(index,1);renderDeck();sync()};
remove.append(button);row.append(name,up,remove);el('deck').append(row)})}
function loadPreset(){const profile=profiles[Number(el('preset').value)];config=structuredClone(profile.spec);el('note').textContent=profile.notes||'';
if(profile.source){const link=document.createElement('a');link.href=profile.source;link.target='_blank';link.rel='noopener';link.textContent=' 查看原對局';el('note').append(link)}render()}
function saveResources(){config.relics=el('relicInput').value.split(',').map(s=>s.trim().toUpperCase()).filter(Boolean);
config.potions=el('potionInput').value.split(',').map(s=>s.trim().toUpperCase()).filter(Boolean);sync()}
function addCard(){const value=el('card').value.trim(),match=catalog.find(c=>c.id===value.toUpperCase()||c.name===value),id=match?match.id:value.toUpperCase();
const copies=Number(el('copies').value),upgrades=Number(el('upgrade').value);if(!id||!Number.isInteger(copies)||copies<1||copies>20)return;
for(let n=0;n<copies;n++)config.deck.push({id,upgrades});renderDeck();sync()}
function readJson(){try{config=JSON.parse(el('spec').value);render()}catch(e){el('operation').textContent=e.message}}
async function send(action){try{el('operation').textContent='處理中…';let payload={action};if(action==='start')payload.spec=config;
const response=await fetch('/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const result=await response.json();
el('operation').textContent=response.ok?'完成；請切回遊戲視窗操作。':result.error}catch(e){el('operation').textContent=e.message}}
fetch('/presets').then(r=>r.json()).then(x=>{profiles=x;x.forEach((p,i)=>{const o=document.createElement('option');o.value=i;o.textContent=p.name;el('preset').append(o)});
el('preset').value=x.length>1?1:0;loadPreset()});
fetch('/catalog').then(r=>r.json()).then(x=>{catalog=x;for(const card of x){const o=document.createElement('option');o.value=card.id;o.label=card.name;el('cards').append(o)}if(config.deck)renderDeck()});
setInterval(async()=>{try{const x=await(await fetch('/status')).json();el('status').textContent=JSON.stringify(x,null,2);
if(x.supported_relics)el('relics').textContent=x.supported_relics.join(', ')}catch(e){el('status').textContent=e.message}},2000);
</script></html>'''


def serve(base, port):
    archive = Archive(OUTPUT)
    lock = threading.Lock()
    status = {'message': 'Connecting to the game'}

    def update(result):
        nonlocal status
        archive.consume(result)
        status = {k: v for k, v in result.items() if k != 'events'}
        status.update(recorded_events=archive.cursor + 1, file=str(archive.path) if archive.path else None)

    def poll():
        nonlocal status
        while True:
            try:
                with lock:
                    result = request(base, after=archive.cursor)
                    if result.get('attempt') != archive.attempt:
                        result = request(base)
                    update(result)
            except Exception as error:
                status = {'error': str(error), 'file': str(archive.path) if archive.path else None}
            time.sleep(1)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, value, code=200, html=False):
            data = value.encode() if html else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/': self.reply(PAGE, html=True)
            elif self.path == '/example': self.reply(EXAMPLE)
            elif self.path == '/presets': self.reply(presets())
            elif self.path == '/catalog': self.reply(card_catalog())
            elif self.path == '/status': self.reply(status)
            else: self.reply({'error': 'Not found'}, 404)

        def do_POST(self):
            if self.path != '/action':
                return self.reply({'error': 'Not found'}, 404)
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 65536: raise ValueError('Invalid request size')
                payload = json.loads(self.rfile.read(size))
                if payload.get('action') not in {'start', 'retry', 'stop'}: raise ValueError('Invalid action')
                with lock:
                    # Flush the old attempt before retry resets the in-game buffer.
                    update(request(base, {'action': 'stop'}) if payload['action'] in {'retry', 'start'}
                           else request(base))
                    update(request(base, payload))
                self.reply(status)
            except Exception as error:
                self.reply({'error': str(error)}, 400)

        def log_message(self, *args): pass

    threading.Thread(target=poll, daemon=True).start()
    print(f'Battle lab: http://127.0.0.1:{port}/', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    server = commands.add_parser('serve')
    server.add_argument('--base', default='http://127.0.0.1:15526')
    server.add_argument('--port', type=int, default=15528)
    comparison = commands.add_parser('compare')
    comparison.add_argument('path')
    comparison.add_argument('--config', choices=['codex:gpt-6.1-sol@low', 'claude:haiku@low'], default='codex:gpt-6.1-sol@low')
    comparison.add_argument('--limit', type=int, default=5)
    args = parser.parse_args()
    if args.command == 'serve': serve(args.base, args.port)
    else:
        if not 1 <= args.limit <= 20: parser.error('--limit must be 1..20')
        print(compare(args.path, args.config, args.limit))


if __name__ == '__main__': main()
