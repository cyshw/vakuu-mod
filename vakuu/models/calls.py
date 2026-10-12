"""Calling the model CLIs: per-fight conversations, one-off calls and the prompt."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import time
import uuid

from core.paths import RUNTIME
from core.rules import END_TURN, LEAVE_SHOP
from models import catalog as models
from models.router import fight_key
from models.session import AgyCombatSession, ClaudeCombatSession, CombatSession

def prune_runs(keep=40, exclude=(), min_age=600):
    # Each call leaves a schema/log folder; keep only recent ones for debugging. Folders younger than
    # min_age may belong to a call still running (replays ask several at once) and are never removed.
    def modified(path):
        try: return path.stat().st_mtime
        except OSError: return None  # Removed meanwhile by another call pruning in parallel.
    runs = sorted(((modified(p), p) for p in RUNTIME.glob('*-*-*-*-*') if p.is_dir()),
                  key=lambda item: item[0] or 0)
    cutoff = time.time() - min_age
    for stamp, old in runs[:-keep]:
        if old not in exclude and stamp is not None and stamp < cutoff: shutil.rmtree(old, ignore_errors=True)

# One file per topic (voice, combat plan, potions, route, shop...), joined in file-name order.
PROMPT = Path(__file__).resolve().parents[1] / 'prompt'

SESSION_MAX_TURNS = 8  # Model calls one combat conversation answers before a fresh one starts.

SESSION_IDLE_SECONDS = 300  # A conversation unused this long is restarted: its prompt cache has likely expired.

REPEATED_RULES = '（遊戲指示與這段對話第一則訊息相同，不再重複，照那份指示判斷。）'

def instructions():
    """Model instructions, read on every call so edits apply without restarting the game."""
    text = ''.join(part.read_text(encoding='utf-8') for part in sorted(PROMPT.glob('*.md')))
    return text.replace('{{END_TURN}}', str(END_TURN)).replace('{{LEAVE_SHOP}}', str(LEAVE_SHOP)).strip()


class ModelCalls:
    """The Controller's model conversations; it sets the lock, stop event and session fields."""

    def close_combat_session(self):
        """End this fight's conversations: the active one and any parked by a model switch."""
        with self.lock:
            sessions = [self.combat_session] + list(self.parked_sessions.values())
            self.combat_session, self.parked_sessions = None, {}
        for session in sessions:
            if session: session.close()

    def sync_combat(self, state):
        previous_key = self.combat_key
        key = fight_key(state)
        # Exhaust/discard prompts are still part of the fight. The API supplies
        # battle data on hand_select, even though ordinary combat actions pause.
        if state.get('state_type') == 'hand_select' and state.get('battle'):
            run = state.get('run') or {}
            key = (run.get('act'), run.get('floor'))
        player = state.get('player') or {}
        # Grid overlays (e.g. Hologram's discard-pile selection) omit battle.
        # The API only includes hand/energy while CombatManager is in progress.
        if state.get('state_type') in {'card_select', 'bundle_select', 'relic_select'} and 'hand' in player and 'energy' in player:
            run = state.get('run') or {}
            key = (run.get('act'), run.get('floor'))
        round_number = (state.get('battle') or {}).get('round')
        restarted = (key is not None and key == self.combat_key
                     and type(round_number) is int and type(self.combat_round) is int
                     and round_number < self.combat_round)
        if key != self.combat_key or restarted: self.close_combat_session()
        self.combat_key = key
        if key is None: self.combat_round = None
        elif round_number is not None or key != previous_key: self.combat_round = round_number

    def call_model(self, prompt, schema, selection, candidate=False):
        started = time.perf_counter()
        agy = selection['model_key'].startswith('agy:')
        # "claude" below means a stream-json CLI kept open per fight: Claude Code or Antigravity's agy.
        claude = selection['model_key'].startswith('claude:') or agy
        # Fights keep one conversation; so does the run of screens between two fights (rewards, card
        # pick, map, event come within a minute), whose rules then need not be resent.
        persistent = (self.combat_key is not None and (claude or selection['model_key'].startswith('codex:'))) or \
                     (self.combat_key is None and claude)
        stale = lambda session: time.monotonic() - getattr(session, 'last_used', time.monotonic()) > SESSION_IDLE_SECONDS
        if self.parked_sessions and any(stale(s) for s in self.parked_sessions.values()):
            with self.lock: old = [k for k, s in self.parked_sessions.items() if stale(s)]
            for key in old: self.parked_sessions.pop(key).close()
        if persistent and self.combat_session and self.combat_session.selection != selection:
            with self.lock:  # Another model's turn: park the active conversation, resume this model's own.
                parked = self.combat_session
                self.parked_sessions[parked.selection['model_key'] + '@' + parked.selection.get('effort', '')] = parked
                self.combat_session = self.parked_sessions.pop(selection['model_key'] + '@' + selection.get('effort', ''), None)
        if self.combat_session and (not persistent or self.combat_session.selection != selection
                                    or (claude and self.combat_session.schema != schema)  # Claude fixes it at start.
                                    or (claude and self.combat_session.turns >= SESSION_MAX_TURNS)
                                    or (claude and stale(self.combat_session))):  # Its cache has likely expired.
            # Claude's CLI keeps its bounded sessions; Codex retains one thread per battle.
            with self.lock: session, self.combat_session = self.combat_session, None
            session.close()
        if not persistent and self.parked_sessions: self.close_combat_session()
        prune_runs(exclude=tuple(s.directory for s in [self.combat_session, *self.parked_sessions.values()] if s))
        run_dir = RUNTIME / str(uuid.uuid4())
        run_dir.mkdir(parents=True)
        schema_path = run_dir / 'schema.json'
        answer_path = run_dir / 'answer.json'
        schema_path.write_text(json.dumps(schema), encoding='utf-8')
        if persistent:
            with self.lock:
                if self.stop_event.is_set(): raise InterruptedError('已停止')
                if self.combat_session is None:
                    self.combat_session = (AgyCombatSession(selection, run_dir, self.stop_event, schema) if agy
                                           else ClaudeCombatSession(selection, run_dir, self.stop_event, schema) if claude
                                           else CombatSession(selection, run_dir, self.stop_event))
                session = self.combat_session
            rules, sent = instructions(), prompt
            if session.turns and prompt.startswith(rules):
                # The conversation already holds the instructions; resending ~6k characters every turn
                # would only grow the context. A fallback call below still gets the whole prompt.
                sent = REPEATED_RULES + prompt[len(rules):]
            try:
                answer = session.ask(sent, schema); session.last_used = time.monotonic()
            except ValueError:
                # A malformed candidate may receive one full-state follow-up
                # in this same conversation, just like an explicit rejection.
                raise
            except InterruptedError:
                self.close_combat_session()
                raise
            except Exception:
                self.close_combat_session()
                if not claude: raise
                session = None  # A broken Claude session falls back to a one-off call below.
            finally:
                if session and session.last_metrics:
                    self.last_call_metrics = {'stage': 'candidate_choice' if candidate else 'full_state',
                        'model': selection, 'prompt_chars': len(sent),
                        'seconds': round(time.perf_counter() - started, 2), **session.last_metrics}
                    (run_dir / 'call-metrics.json').write_text(json.dumps(self.last_call_metrics, ensure_ascii=False), encoding='utf-8')
            if session:
                answer_path.write_text(json.dumps(answer, ensure_ascii=False), encoding='utf-8')
                return answer
        command,provider=models.build_command(selection,run_dir,schema)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with self.lock:
            if self.stop_event.is_set(): raise InterruptedError('已停止')
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                            errors='replace', creationflags=flags,cwd=run_dir)
            process = self.process
        try:
            out, err = process.communicate(models.stdin_for(provider, prompt), timeout=180)
        except subprocess.TimeoutExpired:
            process.kill(); process.communicate()
            raise RuntimeError('模型回覆逾時，請稍後重試')
        finally:
            with self.lock: self.process = None
        if self.stop_event.is_set(): raise InterruptedError('已停止')
        (run_dir / 'codex.log').write_text(out + '\n' + err,encoding='utf-8')
        if process.returncode != 0:
            raise RuntimeError(provider+' 呼叫失敗：' + (err or out)[-700:])
        self.last_call_metrics = {'stage': 'candidate_choice' if candidate else 'full_state',
                                  'model': selection, 'prompt_chars': len(prompt),
                                  'seconds': round(time.perf_counter() - started, 2),
                                  'usage': models.token_usage(provider, out)}
        (run_dir / 'call-metrics.json').write_text(json.dumps(self.last_call_metrics, ensure_ascii=False), encoding='utf-8')
        parser = models.parse_json_answer if candidate else models.parse_answer
        return parser(provider,out,answer_path)
