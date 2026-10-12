"""Vakuu takeover engine and the HTTP bridge used by the in-game panel."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from vakuu_paths import RUNTIME
import sts2_control as api
from sts2_advisor import advise
import spire_models as models
from spire_session import AgyCombatSession, ClaudeCombatSession, CombatSession
import spire_quota
from vakuu_facts import compact_poison
from vakuu_triggers import compact_triggers
from vakuu_relics import compact_relics
from vakuu_facts import combat_facts, deck_profile, deck_summary, enemy_ids, enemy_notes, pile_contents, pull_keywords, rest_facts, route_ahead, route_facts, wastes_energy_card
from spire_codex import event_advice, monster_intel
from vakuu_plans import auto_decision, first_card, hand_signatures, plan_decision, rest_decision, route_decision, start_plan, start_route
from vakuu_router import fight_key, hard_screen, undecided
from vakuu_search import search_turn, turn_candidates
from vakuu_choices import choice_offer, chosen_decision
from vakuu_deck import deck_direction
from vakuu_rules import (COMBAT, END_TURN, FIELDS, LEAVE_SHOP, SCHEMA, allowed_actions, fingerprint, potion_belt_full,
                         still_valid, transitional, validate)

# The same action sent this many times on an unchanged screen means the game is ignoring it.
REPEAT_LIMIT = 3


def execution_busy(state):
    execution = state.get('execution')
    if not isinstance(execution, dict): return False  # Older mod compatibility.
    return execution.get('running') is True or execution.get('queue_empty') is False


def combat_choice(state):
    # Paused actions cannot complete until the player answers these prompts.
    return state.get('state_type') in {'hand_select', 'card_select', 'bundle_select', 'relic_select'}

# Panel buttons: help with what is on screen, play this fight, or play the whole run.
SCOPE_STEPS = {'screen': 40, 'fight': 400, 'run': 5000}
# Screens that belong to one decision (a shop's removal opens card_select, a reward opens card_reward).
SCREEN_GROUPS = {'shop': {'shop', 'card_select'}, 'rewards': {'rewards', 'card_reward', 'card_select'},
                 'card_reward': {'card_reward', 'rewards'}, 'rest_site': {'rest_site', 'card_select'},
                 'event': {'event', 'card_select', 'hand_select'}, 'treasure': {'treasure', 'relic_select'}}

def in_fight(state):
    """A fight screen, including prompts that open inside it (discard/exhaust picks, Hologram's grid)."""
    kind = state.get('state_type')
    player = state.get('player') or {}
    return kind in COMBAT or kind == 'hand_select' or (
        kind in ('card_select', 'bundle_select', 'relic_select') and 'hand' in player and 'energy' in player)

def same_screen(start, now):
    """Whether the decision begun on start is still going (same turn of a fight, same shop...)."""
    kind = start.get('state_type')
    if kind in COMBAT:
        return in_fight(now) and (now.get('battle') or {}).get('round') in (None, (start.get('battle') or {}).get('round'))
    if kind == 'map': return now.get('state_type') == 'map' and (now.get('run') or {}).get('floor') == (start.get('run') or {}).get('floor')
    return now.get('state_type') in SCREEN_GROUPS.get(kind, {kind})

def screen_label(state):
    """What "help me choose" means on this screen, for the panel button."""
    kind = state.get('state_type')
    if kind in COMBAT or kind == 'hand_select': return '幫我打這回合'
    return {'map': '幫我畫路線', 'card_reward': '幫我選牌', 'shop': '幫我逛商店', 'rest_site': '幫我選休息',
            'event': '幫我選事件', 'rewards': '幫我領獎勵', 'card_select': '幫我選牌', 'treasure': '幫我開寶箱'}.get(kind, '幫我選')

# Tuned for the game's Instant fast mode; wait_until_settled covers screens that still animate.
ACTION_PAUSE = 0.5
POLL_INTERVAL = 0.25
CARD_POLL_INTERVAL = 0.1
CARD_SETTLE_WINDOW = 0.2

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

PROMPT = ROOT / 'vakuu_prompt.md'
SESSION_MAX_TURNS = 8  # Model calls one combat conversation answers before a fresh one starts.
SESSION_IDLE_SECONDS = 300  # A conversation unused this long is restarted: its prompt cache has likely expired.
REPEATED_RULES = '（遊戲指示與這段對話第一則訊息相同，不再重複，照那份指示判斷。）'

def instructions():
    """Model instructions, read on every call so edits apply without restarting the game."""
    text = PROMPT.read_text(encoding='utf-8')
    return text.replace('{{END_TURN}}', str(END_TURN)).replace('{{LEAVE_SHOP}}', str(LEAVE_SHOP)).strip()

class Controller:
    def __init__(self, report, allow_model=True, program_comparison=False, program_first=False):
        self.report = report
        self.program_comparison = program_comparison
        self.program_first = program_first
        self.allow_model = allow_model and not program_comparison
        self.stop_event = threading.Event()
        self.process = None
        self.lock = threading.Lock()
        self.strategy = None  # {'fight': fight_key, 'text': battle plan} in auto:brief mode.
        self.route = None  # Planned map route; kept across takeovers until something calls for a re-plan.
        self.want_strategy = False
        self.local_search = None  # (state fingerprint, result), shared with the model prompt.
        self.combat_session = None
        # Auto mode switches Haiku/Sonnet within a fight; each keeps its conversation for when it returns.
        self.parked_sessions = {}
        self.combat_key = None
        self.combat_round = None

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

    def stop(self):
        self.stop_event.set()
        self.close_combat_session()
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
            if self.stop_event.wait(POLL_INTERVAL): raise InterruptedError('已停止')
            state = api.request('/api/v1/singleplayer?format=json')
        return state

    def wait_for_change(self, before, timeout=10, body=None):
        # Some actions (e.g. map travel) keep showing the old screen for a moment; acting on it
        # repeats the move or trips the stale-state check.
        # Effects also land in stages (Capacitor updates the hand first and orb slots later).
        # Cards use fast polling plus a quiet window after the hand changes; other
        # actions retain the screen-transition pause.
        deadline = time.monotonic() + timeout
        playing_card = bool(body and body.get('action') == 'play_card')
        if not playing_card and self.stop_event.wait(ACTION_PAUSE): return
        poll_interval = CARD_POLL_INTERVAL if playing_card else POLL_INTERVAL
        previous = fingerprint(before)
        changed = False
        stable_since = time.monotonic()
        pending_card = False
        pending_discount = False
        discount_hand = []
        if playing_card:
            card = next((c for c in (before.get('player') or {}).get('hand') or []
                         if c.get('index') == body['card_index']), None)
            pending_card = card is not None and not wastes_energy_card(before, card)
            relics = (before.get('player') or {}).get('relics') or []
            if card and card.get('type') == 'Power' and any(
                    str(r.get('id') or '').split('.')[-1] == 'MUMMIFIED_HAND' for r in relics):
                discount_hand = [c for c in before['player']['hand'] if c is not card]
                # An already-free hand has no observable discount to wait for.
                pending_discount = any(str(c.get('cost') or '0').isdigit() and int(c.get('cost') or 0) > 0
                                       or (c.get('star_cost') or 0) > 0 for c in discount_hand)
        old_hand = hand_signatures(before) if pending_card else None
        resolved = not pending_card
        has_execution = False
        state = before
        while time.monotonic() < deadline:
            state = api.request('/api/v1/singleplayer?format=json')
            current = fingerprint(state)
            execution = state.get('execution')
            has_execution = isinstance(execution, dict) and {'running', 'queue_empty'} <= execution.keys()
            # Energy can be spent before the card leaves the hand or its block
            # lands. Playability flags alone do not mean resolution is finished.
            resolved = resolved or state.get('state_type') != before.get('state_type') or hand_signatures(state) != old_hand
            # A draw can replace the played card with an identical copy, leaving
            # hand signatures unchanged. The completed queue is authoritative.
            if has_execution and (changed or current != previous) and (not execution_busy(state) or combat_choice(state)):
                return
            if pending_discount:
                hand = (state.get('player') or {}).get('hand') or []
                # The relic resolves after the power leaves the hand. Require an
                # actual free-cost update, not merely energy spent/card removal.
                if state.get('state_type') != before.get('state_type') or any(
                        old.get('id') == new.get('id') and old.get('name') == new.get('name')
                        and (old.get('cost'), old.get('star_cost')) != (new.get('cost'), new.get('star_cost'))
                        and str(new.get('cost') or '0') == '0' and not new.get('star_cost')
                        for old, new in zip(discount_hand, hand)):
                    pending_discount = False
                    stable_since = time.monotonic()
            if current != previous or not resolved: stable_since = time.monotonic()
            if not has_execution and changed and resolved and not pending_discount and current == previous and time.monotonic() - stable_since >= (CARD_SETTLE_WINDOW if playing_card else 0): return
            changed = changed or current != previous
            previous = current
            if self.stop_event.wait(poll_interval): return
        if not resolved: raise RuntimeError('出牌仍未完成結算，已停止，避免重複出牌。')
        if has_execution and execution_busy(state) and not combat_choice(state):
            raise RuntimeError('遊戲動作佇列仍在結算，已停止，避免使用過期局面。')
        if has_execution: return
        if pending_discount: raise RuntimeError('干癟之手費用效果仍未完成結算，已停止，避免使用過期局面。')

    def wait_until_settled(self, state, timeout=10):
        # Right after resting or claiming the last reward, the screen has nothing to choose and the
        # proceed button is still animating in; asking the model then makes it give up.
        deadline = time.monotonic() + timeout
        while (transitional(state) or (execution_busy(state) and not combat_choice(state))) and time.monotonic() < deadline:
            if self.stop_event.wait(POLL_INTERVAL): raise InterruptedError('已停止')
            state = api.request('/api/v1/singleplayer?format=json')
        if execution_busy(state) and not combat_choice(state):
            raise RuntimeError('遊戲動作佇列仍在結算，已停止，避免使用過期局面。')
        return state

    def decide_routed(self, state):
        """Pick the model; in auto mode escalate to the next tier when one cannot decide."""
        selection = models.settings()
        self.want_strategy = False
        if not selection['model_key'].startswith('auto:'):
            return self.decide(state, selection)
        mode = selection['model_key'].split(':', 1)[1]
        fight = fight_key(state)
        self.want_strategy = mode == 'brief' and fight is not None and (self.strategy or {}).get('fight') != fight
        strong = hard_screen(state) or self.want_strategy or (mode == 'combat' and fight is not None)
        tiers = models.AUTO_TIERS[1 if strong else 0:]
        for tier, stronger in zip(tiers, tiers[1:] + (None,)):
            try:
                decision = self.decide(state, {**selection, 'model_key': 'claude:' + tier})
                reason = '沒有給出可執行的動作' if undecided(state, decision) else None
            except ValueError as exc:  # Malformed answer; login, quota or network errors are not retried.
                if stronger is None: raise
                reason = str(exc)
            if reason is None or stronger is None:
                if self.want_strategy and decision.get('strategy'):
                    self.strategy = {'fight': fight, 'text': decision['strategy']}
                decision['advice'] += f'\n（{tier.title()} 判斷）'
                return decision  # The top tier's answer stands even if it gives up, so takeover stops.
            self.report(f'{tier.title()} {reason}，改由 {stronger.title()} 判斷……')

    def decide(self, state, selection=None):
        self.sync_combat(state)
        selection = selection or models.settings()
        cached = self.local_search
        result = cached[1] if cached and cached[0] == fingerprint(state) else None
        # Candidate-only prompts hide the full hand and cost a second call when rejected; they stay
        # limited to Codex, whose per-battle conversation makes that follow-up cheap, until measured live.
        candidate_mode = selection['model_key'].startswith('codex:') and not self.want_strategy
        offer = choice_offer(state, result) if candidate_mode else None
        rejection = None
        if offer:
            try:
                answer = self.call_model(offer['prompt'], offer['schema'], selection, candidate=True)
                decision = chosen_decision(state, offer, answer)
                if decision is not None: return decision
                rejection = {'reason': answer['reject_reason'], 'detail': answer['reason'][:120]}
            except ValueError as exc:
                rejection = {'reason': 'invalid_candidate_response', 'detail': str(exc)}
            if self.stop_event.is_set(): raise InterruptedError('已停止')
            self.report('候選需要完整局面判斷，補上手牌後再問一次。')
        compact = api.compact(state)
        if rejection: compact['candidate_rejection'] = rejection
        # Deck identities are essential for decisions; pile order is not needed.
        if 'player' in state and 'deck' in state['player']:
            compact['player']['deck'] = deck_summary(state['player']['deck'])
        if state.get('state_type') == 'card_reward': compact['card_advice'] = advise(state)
        if state.get('state_type') == 'shop': compact['shop_card_advice'] = advise(state)
        if state.get('state_type') == 'map':
            compact['route_facts'] = route_facts(state)
            # The candidate routes already carry every room and coordinate; the raw ~60-node graph
            # doubled the map prompt (about 4.6k characters).
            compact.get('map', {}).pop('nodes', None)
        if state.get('state_type') == 'rest_site': compact['rest_facts'] = rest_facts(state, self.route)
        if state.get('state_type') == 'rewards' and potion_belt_full(state):
            compact['potion_belt_full'] = '藥水欄已滿：要領藥水得先 discard_potion 丟掉一瓶較差的，否則不領，proceed 離開'
        if state.get('state_type') == 'event':
            event = state.get('event') or {}
            advice = event_advice(event.get('event_id'), event.get('options') or [],
                                  (state.get('player') or {}).get('character_id') or '')
            if advice: compact['event_advice'] = advice
            ahead = route_ahead(self.route)
            if ahead: compact['route_ahead'] = ahead
        if state.get('state_type') in ('card_reward', 'shop', 'card_select', 'event'):
            compact['deck_size'] = len((state.get('player') or {}).get('deck') or [])
            compact['deck_profile'] = deck_profile((state.get('player') or {}).get('deck') or [], (state.get('run') or {}).get('act'))
            compact['deck_direction'] = deck_direction(state)[0]
        if self.want_strategy: compact['request_strategy'] = True
        elif self.strategy and self.strategy['fight'] == fight_key(state): compact['fight_strategy'] = self.strategy['text']
        if state.get('state_type') in COMBAT:
            cached = self.local_search
            result = cached[1] if cached and cached[0] == fingerprint(state) else None
            candidates = None if rejection else turn_candidates(state, result)
            if candidates: compact['turn_candidates'] = candidates
            compact['combat_facts'] = combat_facts(state)
            compact_poison(compact, state)
            compact_triggers(compact, state)
            compact_relics(compact, state)
            piles = pile_contents(state.get('player') or {})
            if piles: compact['pile_contents'] = piles
            notes = enemy_notes(state)
            if notes: compact['enemy_notes'] = notes
            ascension = (state.get('run') or {}).get('ascension') or 0
            intel = {enemy: monster_intel(enemy, ascension) for enemy in enemy_ids(state)}
            if any(intel.values()): compact['enemy_intel'] = {k: v for k, v in intel.items() if v}
        # Only this screen's actions: the full list let the model pick look-alikes from other screens.
        compact['keyword_glossary'] = pull_keywords(compact)
        actions = {a: sorted(FIELDS[a]) for a in sorted(allowed_actions(state))}
        prompt = (instructions() + '\n目前畫面可用動作與參數：' + json.dumps(actions, ensure_ascii=False)
                  + '\n遊戲狀態：' + json.dumps(compact, ensure_ascii=False))
        return self.call_model(prompt, SCHEMA, selection)

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

    def run(self, takeover=False, max_steps=30, finish_candidate=False, scope=None):
        """scope: None (max_steps), 'screen' (only the decision on screen now), 'fight' or 'run'."""
        opened_cards = set()
        plan = None
        repeats = (None, 0)  # (screen fingerprint and action sent, times in a row)
        start = None  # The state a scoped takeover began on.
        if scope in SCOPE_STEPS: max_steps = SCOPE_STEPS[scope]
        try:
            # Test runs may drain a selected bounded prefix after the step cap,
            # but may not ask another model or start a new plan in these steps.
            for step in range((max_steps + (3 if finish_candidate else 0)) if takeover else 1):
                if self.stop_event.is_set(): break
                draining = takeover and step >= max_steps
                if draining and not (plan and plan.get('candidate_id') and plan.get('queue')): break
                state = api.request('/api/v1/singleplayer?format=json')
                if self.stop_event.is_set(): break
                # Settle first: a room that is still loading would skip the play-phase wait, and a fight
                # that just began has no hand yet.
                if takeover: state = self.wait_until_settled(self.wait_for_play_phase(self.wait_until_settled(state)))
                if self.stop_event.is_set(): break
                self.sync_combat(state)  # Also observe battles completed entirely by local decisions.
                if state.get('state_type') in {'menu','game_over','unknown','overlay'}:
                    self.report('先踏進尖塔，我才有施展的餘地。\n請先進入遊戲，或完成目前需要手動處理的畫面。'); break
                if start is None:
                    start = state
                    if scope == 'fight' and not in_fight(state):
                        self.report('現在沒有戰鬥可接手。\n進入戰鬥後再按「接手這場戰鬥」。'); break
                elif scope == 'screen' and not same_screen(start, state):
                    self.report('這一步辦完了，控制權還你。\n已完成「' + screen_label(start) + '」。'); break
                elif scope == 'fight' and not in_fight(state):
                    self.report('這場戰鬥結束了，戰利品留給你挑。\n已停止接手。'); break
                self.report('完成已選候選的剩餘動作。' if draining else
                            '我有一份提案……容我看看你的牌。' if not takeover else
                            f'把控制權交給我。你會喜歡結果的。\n接手中：第 {step+1} 步／{max_steps} 步')
                asked_model = False  # Only a model's answer is worth asking again when it is malformed.
                # A selected candidate owns its queue. Verify it before any new
                # local optimization can replace the model's selected order.
                decision = plan_decision(plan, state) if takeover and plan and (plan.get('candidate_id') or self.program_first) else None
                if draining and decision is None: break
                if takeover and plan and (plan.get('candidate_id') or self.program_first) and decision is None: plan = None
                if takeover and decision is None: decision = auto_decision(state, opened_cards)
                self.local_search = None
                if takeover and decision is None and state.get('state_type') in COMBAT:
                    result = search_turn(state, include_candidates=True)
                    self.local_search = (fingerprint(state), result)
                    # Normal takeover delegates non-winning turn choices to the model.
                    # The explicit program-only experiment accepts fully simulated turns too.
                    if result['reason'] == 'direct_win' or ((self.program_comparison or self.program_first) and result['reason'] == 'supported_turn'):
                        decision = result['decision']
                        plan = None  # Re-read and search after each executed move.
                if takeover and decision is None:
                    decision = (rest_decision(self.route, state) or route_decision(self.route, state)
                                or plan_decision(plan, state))
                if decision is None:
                    if not self.allow_model:
                        self.report('程式無法可靠處理目前局面，已停止接手。\n此模式不會呼叫 AI，請手動選擇。')
                        break
                    if self.program_first and self.local_search:
                        self.report('程式需要 AI 判斷：' + self.local_search[1]['reason'])
                    decision = self.decide_routed(state)
                    asked_model = True
                    if state.get('state_type') == 'map': self.route = start_route(state, decision)
                    # Buying card removal opens a selection screen; keep the shopping list for afterwards.
                    keep = plan and plan['kind'] == 'shop' and state.get('state_type') == 'card_select'
                    plan = start_plan(state, decision) or (plan if keep else None)
                    first = first_card(state, decision)
                    if first and wastes_energy_card(state, first) and plan and plan['kind'] == 'turn':
                        plan['expected'][first.get('name')] += 1  # It stays in hand instead of being played.
                        decision = plan_decision(plan, state) or decision  # Skip the wasted energy card.
                elif plan and not plan['queue']: plan = None
                self.report(decision['advice'])
                if decision['stop'] or decision['action'] == 'none':
                    self.close_combat_session()
                    break
                if not takeover: break
                try: body = validate(state, decision['action'], json.loads(decision['parameters_json'] or '{}'))
                except ValueError as exc:
                    if not self.allow_model or not asked_model: raise
                    # Gemini 3.8 Flash once sent play_card with parameters_json "{}" (the card was only in its
                    # advice). One retry that names the mistake costs a call; stopping takeover costs the fight.
                    self.report('答案格式有誤，請模型重答一次：' + str(exc))
                    decision = self.decide_routed({**state, 'previous_answer_error':
                        f"上一個答案無法執行（{exc}）：{decision['action']} {decision['parameters_json']}。請重新回答，parameters_json 要包含這個動作的必要參數。"})
                    if state.get('state_type') == 'map': self.route = start_route(state, decision)
                    plan = start_plan(state, decision)
                    body = validate(state, decision['action'], json.loads(decision['parameters_json'] or '{}'))
                key = (fingerprint(state), json.dumps(body, sort_keys=True))
                repeats = (key, repeats[1] + 1 if repeats[0] == key else 1)
                if repeats[1] >= REPEAT_LIMIT:
                    # A claimed potion with a full belt left the screen unchanged and was asked for again
                    # every 15 seconds; stop instead of spending model calls on an action the game ignores.
                    self.close_combat_session()
                    self.report('同一個動作送了幾次，畫面都沒有變化。\n已停止接手，請手動處理這個畫面。'); break
                current = api.request('/api/v1/singleplayer?format=json')
                if (execution_busy(current) and not combat_choice(current)) or not still_valid(state, current, body):
                    self.close_combat_session()
                    self.report('看來你另有打算。控制權歸你了。\n局面已改變，已停止接手。'); break
                with self.lock:
                    if self.stop_event.is_set(): break
                    result = api.request('/api/v1/singleplayer', body)
                if result.get('status') != 'ok': raise RuntimeError(str(result))
                self.report(decision['advice'] + '\n〔已執行〕')
                self.wait_for_change(current, body=body)
                if self.combat_session:
                    self.sync_combat(api.request('/api/v1/singleplayer?format=json'))
            else:
                self.report(f'這份契約，暫且履行到此。\n已完成 {max_steps} 步並停止；需要時可再次呼叫。')
        except InterruptedError:
            self.close_combat_session()
            self.report('也好，讓我看看你的本事。\n已停止接手，控制權歸你。')
        except Exception as exc:
            self.close_combat_session()
            self.report('這份交易遇到了些阻礙。\n已停止：' + str(exc))
        finally:
            if self.stop_event.is_set(): self.report('也好，讓我看看你的本事。\n已停止接手，控制權歸你。')
            self.report('__DONE__')

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
                self.respond(spire_quota.quota(key)); return
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

if __name__=='__main__': main()
