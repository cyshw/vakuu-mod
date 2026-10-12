"""One ephemeral Codex app-server process and conversation per combat."""
import json
import os
import queue
import subprocess
import threading
import time
from models import catalog as models



class CombatSession:
    def __init__(self, selection, directory, stop_event):
        self.selection = dict(selection)
        self.directory = directory
        self.stop_event = stop_event
        self.thread_id = None
        self.initialized = False
        self.request_id = 0
        self.turns = 0
        self.last_metrics = None
        self.messages = queue.Queue()
        self.pending = []
        self.closed = threading.Event()
        self.process = subprocess.Popen(
            [models.find_cli('codex'), 'app-server', '--listen', 'stdio://'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', cwd=directory,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.readers = [threading.Thread(target=self._read_stdout, daemon=True),
                        threading.Thread(target=self._read_stderr, daemon=True)]
        for reader in self.readers: reader.start()

    def _read_stdout(self):
        try:
            with (self.directory / 'session-events.jsonl').open('w', encoding='utf-8') as log:
                for line in self.process.stdout:
                    log.write(line)
                    log.flush()
                    try: message = json.loads(line)
                    except ValueError: continue
                    if isinstance(message, dict): self.messages.put(message)
        finally:
            self.messages.put({'closed': True})

    def _read_stderr(self):
        with (self.directory / 'session-stderr.log').open('w', encoding='utf-8') as log:
            for line in self.process.stderr:
                log.write(line)
                log.flush()

    def close(self):
        self.closed.set()
        if self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for reader in self.readers: reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream: stream.close()

    def _check_stop(self):
        if self.stop_event.is_set(): raise InterruptedError('已停止')
        if self.closed.is_set(): raise RuntimeError('戰鬥對話已關閉')

    def _send(self, message):
        self._check_stop()
        try:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + '\n')
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            self._check_stop()
            raise RuntimeError('Codex 戰鬥對話已中斷') from exc

    def _next(self, deadline):
        while True:
            self._check_stop()
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise RuntimeError('模型回覆逾時，請稍後重試')
            try: message = self.messages.get(timeout=min(.25, remaining))
            except queue.Empty: continue
            if message.get('closed'):
                self._check_stop()
                raise RuntimeError('Codex 戰鬥對話已中斷；請檢查 session-stderr.log')
            if 'id' in message and 'method' in message:
                # This process only selects game actions. It cannot grant tools,
                # approvals or access to connected applications.
                self._send({'id': message['id'], 'error': {
                    'code': -32601, 'message': 'Game advisor does not support tool requests'}})
                raise RuntimeError('戰鬥顧問要求了非遊戲工具，已停止')
            return message

    def _rpc(self, method, params, deadline):
        self.request_id += 1
        self._send({'id': self.request_id, 'method': method, 'params': params})
        while True:
            message = self._next(deadline)
            if message.get('id') == self.request_id:
                if 'error' in message: raise RuntimeError('Codex 呼叫失敗：' + str(message['error'])[-700:])
                return message['result']
            self.pending.append(message)

    def ask(self, prompt, schema, timeout=180):
        self.last_metrics = None
        deadline = time.monotonic() + timeout
        if not self.initialized:
            self._rpc('initialize', {'clientInfo': {'name': 'vakuu-combat', 'version': '1.0'},
                                     'capabilities': {'experimentalApi': True}}, deadline)
            self._send({'method': 'initialized'})
            self.initialized = True
            params = {'ephemeral': True, 'cwd': str(self.directory), 'sandbox': 'read-only',
                      'approvalPolicy': 'never', 'environments': []}
            model = self.selection['model_key'].split(':', 1)[1]
            if model: params['model'] = model
            self.thread_id = self._rpc('thread/start', params, deadline)['thread']['id']
        params = {'threadId': self.thread_id, 'environments': [], 'outputSchema': schema,
                  'input': [{'type': 'text', 'text':
                      '本次局面是最新狀態；忽略舊局面的候選編號、手牌索引與敵人意圖。只依本次提供的格式回覆。\n' + prompt}]}
        if self.selection.get('effort'): params['effort'] = self.selection['effort']
        turn_id = self._rpc('turn/start', params, deadline)['turn']['id']
        text = None
        usage = None
        total_usage = None
        while True:
            message = self.pending.pop(0) if self.pending else self._next(deadline)
            self._check_stop()
            if time.monotonic() >= deadline: raise RuntimeError('模型回覆逾時，請稍後重試')
            values = message.get('params') or {}
            if values.get('threadId') != self.thread_id: continue
            method = message.get('method')
            if method == 'thread/tokenUsage/updated' and values.get('turnId') == turn_id:
                usage = normalize_usage(values['tokenUsage'].get('last'))
                total_usage = normalize_usage(values['tokenUsage'].get('total'))
            elif method == 'item/completed' and values.get('turnId') == turn_id:
                item = values['item']
                if item['type'] == 'agentMessage': text = item['text']
                elif item['type'] not in {'userMessage', 'reasoning', 'plan'}:
                    raise RuntimeError('戰鬥顧問執行了非遊戲工具，已停止')
            elif method == 'turn/completed' and values['turn']['id'] == turn_id:
                if values['turn']['status'] != 'completed':
                    raise RuntimeError('Codex 回覆失敗：' + str(values['turn'].get('error'))[-700:])
                break
        self.turns += 1
        self.last_metrics = {'transport': 'combat_session', 'thread_id': self.thread_id,
                             'turn_id': turn_id, 'session_turn': self.turns,
                             'usage': usage, 'session_total_usage': total_usage}
        if text is None: raise ValueError('Codex 沒有回傳戰鬥決策')
        return json.loads(text)


def normalize_usage(value):
    if not isinstance(value, dict): return None
    names = {'inputTokens': 'input_tokens', 'cachedInputTokens': 'cached_input_tokens',
             'outputTokens': 'output_tokens', 'reasoningOutputTokens': 'reasoning_output_tokens',
             'totalTokens': 'total_tokens', 'cacheWriteInputTokens': 'cache_write_input_tokens'}
    result = {name: value[key] for key, name in names.items()
              if type(value.get(key)) is int and value[key] >= 0}
    return result or None


class ClaudeCombatSession:
    """One Claude CLI process per combat, kept open in stream-json mode.

    A fresh `claude -p` per decision paid ~2.4s of startup and wrote ~7.4k prompt tokens to the
    cache that no later call read. Kept open, the CLI starts once per fight and each turn reads the
    earlier turns from the cache. The JSON schema is fixed when the process starts, so a call with
    another schema needs a new session.
    """

    def __init__(self, selection, directory, stop_event, schema):
        self.selection = dict(selection)
        self.directory = directory
        self.stop_event = stop_event
        self.schema = schema
        self.turns = 0
        self.last_metrics = None
        self.messages = queue.Queue()
        self.closed = threading.Event()
        command = self._command(selection, directory, schema)
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', cwd=directory,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.readers = [threading.Thread(target=self._read_stdout, daemon=True),
                        threading.Thread(target=self._read_stderr, daemon=True)]
        for reader in self.readers: reader.start()

    def _read_stdout(self):
        try:
            with (self.directory / 'session-events.jsonl').open('w', encoding='utf-8') as log:
                for line in self.process.stdout:
                    log.write(line)
                    log.flush()
                    try: message = json.loads(line)
                    except ValueError: continue
                    if isinstance(message, dict) and self._is_result(message): self.messages.put(message)
        finally:
            self.messages.put({'closed': True})

    def _read_stderr(self):
        with (self.directory / 'session-stderr.log').open('w', encoding='utf-8') as log:
            for line in self.process.stderr:
                log.write(line)
                log.flush()

    def close(self):
        self.closed.set()
        if self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for reader in self.readers: reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream: stream.close()

    def ask(self, prompt, schema, timeout=180):
        self.last_metrics = None
        if schema != self.schema: raise RuntimeError('Claude 戰鬥對話的輸出格式不同，需要新對話')
        if self.stop_event.is_set(): raise InterruptedError('已停止')
        if self.closed.is_set(): raise RuntimeError('戰鬥對話已關閉')
        text = '本次局面是最新狀態；忽略舊局面的手牌索引與敵人意圖。只依本次提供的格式回覆。\n' + prompt
        try:
            self.process.stdin.write(self._message(text))
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise RuntimeError('Claude 戰鬥對話已中斷') from exc
        deadline = time.monotonic() + timeout
        while True:
            if self.stop_event.is_set(): raise InterruptedError('已停止')
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise RuntimeError('模型回覆逾時，請稍後重試')
            try: message = self.messages.get(timeout=min(.25, remaining))
            except queue.Empty: continue
            if message.get('closed'): raise RuntimeError('Claude 戰鬥對話已中斷；請檢查 session-stderr.log')
            break
        self.turns += 1
        return self._answer(message)

    # The CLI-specific parts; AgyCombatSession replaces them.
    def _command(self, selection, directory, schema):
        command, _ = models.build_command(selection, directory, schema)
        at = command.index('--output-format')
        command[at:at + 2] = ['--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose']
        return command

    def _is_result(self, message):
        return message.get('type') == 'result'

    def _message(self, text):
        return json.dumps({'type': 'user', 'message': {'role': 'user', 'content': text}}, ensure_ascii=False) + '\n'

    def _answer(self, message):
        usage = message.get('usage') or {}
        self.last_metrics = {'transport': 'claude_session', 'session_turn': self.turns,
                             'api_ms': message.get('duration_api_ms'), 'duration_ms': message.get('duration_ms'),
                             'usage': {k: usage.get(k) for k in ('input_tokens', 'cache_read_input_tokens',
                                                                 'cache_creation_input_tokens', 'output_tokens')}}
        return models.parse_answer('claude', json.dumps(message, ensure_ascii=False), None)


class AgyCombatSession(ClaudeCombatSession):
    """One Antigravity (agy) process per combat; same stream-json idea, its own event format.

    agy reports usage cumulatively for the session, so a turn's own usage is the difference.
    """

    def __init__(self, *args, **kwargs):
        self.previous_usage = {}
        super().__init__(*args, **kwargs)

    def _command(self, selection, directory, schema):
        return models.build_command(selection, directory, schema)[0]  # Already stream-json in and out.

    def _is_result(self, message):
        return message.get('event') == 'result'

    def _message(self, text):
        return models.stdin_for('agy', text)

    def _answer(self, message):
        result = message.get('result') or {}
        total = result.get('usage') or {}
        turn = {k: v - self.previous_usage.get(k, 0) for k, v in total.items() if type(v) is int}
        self.previous_usage = total
        self.last_metrics = {'transport': 'agy_session', 'session_turn': self.turns, 'usage': turn}
        return models.parse_answer('agy', json.dumps(message, ensure_ascii=False), None)
