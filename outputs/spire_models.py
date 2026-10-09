import json
import os
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PREFS=ROOT/'button-runtime'/'model-settings.json'
# Verified against Claude Code 2.1.295 and official model configuration.
# Pin IDs so the displayed version and the requested version stay consistent.
CLAUDE_MODEL_IDS={m:'claude-'+m+'-5-5' for m in ('sonnet','opus','haiku')}
MODELS=[{'key':'codex:','label':'Codex · 預設模型'}]+[
    {'key':'codex:'+m,'label':'Codex · '+m} for m in
    ['gpt-6.1-sol','gpt-6-astra','gpt-6-sol','gpt-6-luna','gpt-5.6-sol','gpt-5.6-terra','gpt-5.6-luna']
]+[{'key':'claude:'+m,'label':'Claude · '+m.title()+' 5.5'} for m in CLAUDE_MODEL_IDS]

def settings():
    try: result=json.loads(PREFS.read_text(encoding='utf-8'))
    except (OSError,ValueError): result={'model_key':'codex:','effort':''}
    return validate_settings(result)

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
             '--model',CLAUDE_MODEL_IDS[model],'--tools','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--no-session-persistence']
    if effort: command+=['--effort',effort]
    return command,provider

def parse_answer(provider,output,answer_path):
    if provider=='codex': return json.loads(answer_path.read_text(encoding='utf-8'))
    envelope=json.loads(output)
    if envelope.get('is_error'): raise RuntimeError(str(envelope.get('result',envelope.get('errors'))))
    value=envelope.get('structured_output')
    if value is None: value=json.loads(envelope.get('result','{}'))
    if not isinstance(value,dict) or set(value)!={'advice','stop','action','parameters_json'}:
        raise ValueError('Claude 沒有傳回有效的遊戲決策')
    return value
