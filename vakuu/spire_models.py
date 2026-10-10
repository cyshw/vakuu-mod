import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PREFS=ROOT/'button-runtime'/'model-settings.json'
# Verified against Claude Code 2.1.295 and official model configuration.
# Pin IDs so the displayed version and the requested version stay consistent.
CLAUDE_MODEL_IDS={m:'claude-'+m+'-5-5' for m in ('sonnet','opus','haiku')}

def claude_auth_status():
    """Return only UI login state; never expose account fields or credentials."""
    try:
        result=subprocess.run([find_cli('claude'),'auth','status'],capture_output=True,
            text=True,encoding='utf-8',timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        status=json.loads(result.stdout)
        logged_in=status.get('loggedIn')
        if type(logged_in) is not bool: return {'logged_in':None}
        return {'logged_in':logged_in}
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired):
        return {'logged_in':None}
MODELS=[{'key':'codex:','label':'Codex · 預設模型'}]+[
    {'key':'codex:'+m,'label':'Codex · '+m} for m in
    ['gpt-6.1-sol','gpt-6-astra','gpt-6-sol','gpt-6-luna','gpt-5.6-sol','gpt-5.6-terra','gpt-5.6-luna']
]+[{'key':'claude:'+m,'label':'Claude · '+m.title()+' 5.5'} for m in CLAUDE_MODEL_IDS]+[
    {'key':'auto:','label':'Claude · 自動調度'},
    # Comparison variants: a Sonnet battle plan per fight for Haiku to follow, or Sonnet for every fight.
    {'key':'auto:brief','label':'Claude · 自動調度（Sonnet 作戰方針）'},
    {'key':'auto:combat','label':'Claude · 自動調度（戰鬥用 Sonnet）'}]
# Auto mode: easy screens use the first tier, hard ones start at the second, and a tier that
# cannot decide hands the same screen to the next one.
AUTO_TIERS=('haiku','sonnet','opus')

def uses_claude(model_key):
    return model_key.startswith(('claude:','auto:'))

DEFAULT_SETTINGS={'model_key':'codex:','effort':''}

def settings():
    # A stale file (e.g. a model removed from MODELS) must not break every bridge endpoint.
    try: return validate_settings(json.loads(PREFS.read_text(encoding='utf-8')))
    except (OSError,ValueError,AttributeError): return dict(DEFAULT_SETTINGS)

def validate_settings(value):
    if value.get('model_key') not in {m['key'] for m in MODELS}: raise ValueError('未知模型')
    if value.get('effort','') not in ('','low','medium','high'): raise ValueError('未知推理強度')
    return {'model_key':value['model_key'],'effort':value.get('effort','')}

def save_settings(value):
    value=validate_settings(value)
    PREFS.parent.mkdir(parents=True,exist_ok=True)
    temporary=PREFS.with_suffix('.tmp')
    temporary.write_text(json.dumps(value),encoding='utf-8')
    temporary.replace(PREFS)
    return value

def find_cli(provider):
    found=shutil.which('claude' if provider=='claude' else 'codex')
    if found and (os.name!='nt' or found.lower().endswith('.exe')): return found
    if provider=='claude':
        for path in [Path.home()/'.local/bin/claude.exe',ROOT/'claude/claude.exe']:
            if path.exists(): return str(path)
    else:
        paths=sorted((Path(os.environ['LOCALAPPDATA'])/'OpenAI/Codex/bin').glob('*/codex.exe'),key=lambda p:p.stat().st_mtime)
        if paths: return str(paths[-1])
    raise RuntimeError('找不到 '+provider+' 程式')

def build_command(selection,run_dir,schema):
    selection=validate_settings(selection)
    provider,model=selection['model_key'].split(':',1)
    if provider=='auto': raise ValueError('自動調度需先決定實際模型')
    effort=selection['effort']
    exe=find_cli(provider)
    if provider=='codex':
        command=[exe,'exec','--ephemeral','--skip-git-repo-check','--sandbox','read-only',
                 '--output-schema',str(run_dir/'schema.json'),'--output-last-message',str(run_dir/'answer.json'),
                 '--color','never','-C',str(run_dir)]
        if model: command+=['--model',model]
        if effort: command+=['-c','model_reasoning_effort='+json.dumps(effort)]
        return command+['-'],provider
    command=[exe,'-p','--output-format','json','--json-schema',json.dumps(schema),
             '--model',CLAUDE_MODEL_IDS[model],'--tools','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--no-session-persistence',
             # Skip user settings, hooks, plugins and skills: measured ~1.6s faster startup per call; OAuth still works.
             '--setting-sources','','--disable-slash-commands']
    if effort: command+=['--effort',effort]
    return command,provider

def parse_answer(provider,output,answer_path):
    if provider=='codex': return json.loads(answer_path.read_text(encoding='utf-8'))
    envelope=json.loads(output)
    if envelope.get('is_error'): raise RuntimeError(str(envelope.get('result',envelope.get('errors'))))
    value=envelope.get('structured_output')
    if value is None: value=json.loads(envelope.get('result','{}'))
    if not isinstance(value,dict) or not set(value)<={'advice','stop','action','parameters_json','next_cards','next_purchases','strategy','next_nodes'} \
            or not {'advice','stop','action','parameters_json'}<=set(value):
        raise ValueError('Claude 沒有傳回有效的遊戲決策')
    if not isinstance(value.get('next_cards',[]),list): raise ValueError('Claude 沒有傳回有效的出牌計畫')
    return value
