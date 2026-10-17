"""The takeover loop: read the screen, decide locally or ask the model, execute and wait."""
import json
import threading
import time

from combat.choices import choice_offer, chosen_decision
from combat.facts import block_damage_notes, combat_facts, compact_poison, hand_verdicts, enemy_ids, enemy_notes, pile_contents, wastes_energy_card
from combat.relics import compact_relics
from combat.search import search_turn, turn_candidates
from combat.triggers import compact_triggers
from core import game_api as api
from core.keywords import pull_keywords
from core.rules import COMBAT, FIELDS, SCHEMA, allowed_actions, fingerprint, potion_belt_full, still_valid, transitional, validate, waiting_for_teammates
from core.screens import combat_choice, execution_busy, has_execution_state, in_fight, same_screen, screen_label
from core.spire_codex import event_advice, monster_intel
from deck.advisor import advise
from deck.direction import deck_direction
from deck.facts import deck_profile, deck_summary
from deck.playbook import deck_playbook
from models import catalog as models
from models.calls import ModelCalls, instructions
from models.router import fight_key, hard_screen, undecided
from route.facts import rest_facts, route_ahead, route_facts
from route.plans import rest_decision, route_decision, start_route
from takeover.plans import auto_decision, first_card, hand_signatures, plan_decision, start_plan

# The same action sent this many times on an unchanged screen means the game is ignoring it.
REPEAT_LIMIT = 3


# Panel buttons: help with what is on screen, play this fight, or play the whole run.
SCOPE_STEPS = {'screen': 40, 'fight': 400, 'run': 5000}


# Tuned for the game's Instant fast mode; wait_until_settled covers screens that still animate.
ACTION_PAUSE = 0.5
POLL_INTERVAL = 0.25
CARD_POLL_INTERVAL = 0.1
CARD_SETTLE_WINDOW = 0.2


class Controller(ModelCalls):
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
        # e.g. 'codex:gpt-6.1-sol@low': writes how the deck is played once per deck (deck/playbook.py).
        self.playbook_model = None

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
        """Prefer action-queue telemetry; isolate old-mod observation heuristics."""
        deadline = time.monotonic() + timeout
        playing_card = bool(body and body.get('action') == 'play_card')
        if not playing_card and self.stop_event.wait(ACTION_PAUSE): return
        state = api.request('/api/v1/singleplayer?format=json')
        if has_execution_state(state) or has_execution_state(before):
            interval = CARD_POLL_INTERVAL if playing_card else POLL_INTERVAL
            return self.wait_for_execution(before, state, deadline, interval)
        return self.wait_for_legacy_change(before, deadline, body, state)

    def wait_for_execution(self, before, state, deadline, interval):
        """A changed state and a completed queue, or a paused player choice, resolve the action."""
        baseline = fingerprint(before)
        changed = False
        while time.monotonic() < deadline:
            if not has_execution_state(state):
                if not in_fight(state) and fingerprint(state) != baseline: return
                # CombatManager can finish before the room shows its rewards.
                # Do not decide on this intermediate monster/battle=null snapshot.
                if state.get('state_type') in COMBAT and state.get('battle') is None:
                    if self.stop_event.wait(interval): return
                    state = api.request('/api/v1/singleplayer?format=json')
                    continue
                raise RuntimeError('遊戲動作佇列狀態消失，已停止，避免使用過期局面。')
            changed = changed or fingerprint(state) != baseline
            if changed and (not execution_busy(state) or combat_choice(state)): return
            if self.stop_event.wait(interval): return
            state = api.request('/api/v1/singleplayer?format=json')
        raise RuntimeError('遊戲動作佇列或局面尚未確認結算，已停止，避免重複動作。')

    def wait_for_legacy_change(self, before, deadline, body=None, first_state=None):
        # Some actions (e.g. map travel) keep showing the old screen for a moment; acting on it
        # repeats the move or trips the stale-state check.
        # Effects also land in stages (Capacitor updates the hand first and orb slots later).
        # Cards use fast polling plus a quiet window after the hand changes; other
        # actions retain the screen-transition pause.
        playing_card = bool(body and body.get('action') == 'play_card')
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
        state = before
        while time.monotonic() < deadline:
            state = first_state if first_state is not None else api.request('/api/v1/singleplayer?format=json')
            first_state = None
            current = fingerprint(state)
            if has_execution_state(state):
                return self.wait_for_execution(before, state, deadline, poll_interval)
            resolved = resolved or state.get('state_type') != before.get('state_type') or hand_signatures(state) != old_hand
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
            if changed and resolved and not pending_discount and current == previous and time.monotonic() - stable_since >= (CARD_SETTLE_WINDOW if playing_card else 0): return
            changed = changed or current != previous
            previous = current
            if self.stop_event.wait(poll_interval): return
        if not resolved: raise RuntimeError('出牌仍未完成結算，已停止，避免重複出牌。')
        if pending_discount: raise RuntimeError('干癟之手費用效果仍未完成結算，已停止，避免使用過期局面。')

    def wait_for_teammates(self, state, timeout=900):
        """In co-op, wait while our end turn or vote is in and the teammates have not acted yet.

        Our move is already submitted, so the screen stays the same; deciding again would resend it
        (the repeat guard would then stop takeover) or undo a vote.
        """
        reported = False
        deadline = time.monotonic() + timeout
        while waiting_for_teammates(state) and time.monotonic() < deadline:
            if not reported:
                self.report('我這邊已經出手，等隊友行動……'); reported = True
            if self.stop_event.wait(1): raise InterruptedError('已停止')
            state = api.request('/api/v1/singleplayer?format=json')
        return state

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
            ahead = route_ahead(self.route, state)
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
            if self.playbook_model:
                model_key, effort = (self.playbook_model.split('@') + ['low'])[:2]
                ask = lambda prompt, schema: self.call_model(prompt, schema, {'model_key': model_key, 'effort': effort},
                                                             candidate=True, one_off=True)
                book = deck_playbook(state, ask)
                if book: compact['deck_playbook'] = book
            # Verdicts on the cards themselves; Haiku missed them when left to compare numbers.
            verdicts = hand_verdicts(state, compact['combat_facts'])
            for card in (compact.get('player') or {}).get('hand') or []:
                if card.get('index') in verdicts: card['skip_this_turn'] = verdicts[card['index']]
            slams = block_damage_notes(state)
            for card in (compact.get('player') or {}).get('hand') or []:
                if card.get('index') in slams: card['real_damage'] = slams[card['index']]
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

    def local_decision(self, state, plan, opened_cards, takeover, draining=False):
        """Priority: selected queue, forced actions, search, rest/route, ordinary queue."""
        decision = plan_decision(plan, state) if takeover and plan and (plan.get('candidate_id') or self.program_first) else None
        if draining and decision is None: return None, None
        if takeover and plan and (plan.get('candidate_id') or self.program_first) and decision is None: plan = None
        self.local_search = None
        if decision is not None or not takeover: return decision, plan
        sources = (
            lambda: auto_decision(state, opened_cards),
            lambda: self.search_decision(state),
            lambda: rest_decision(self.route, state),
            lambda: route_decision(self.route, state),
            lambda: plan_decision(plan, state),
        )
        for source in sources:
            decision = source()
            if decision is not None:
                if self.local_search and decision is self.local_search[1].get('decision'): plan = None
                break
        return decision, plan

    def search_decision(self, state):
        if state.get('state_type') not in COMBAT: return None
        result = search_turn(state, include_candidates=True)
        self.local_search = (fingerprint(state), result)
        if result['reason'] == 'direct_win' or ((self.program_comparison or self.program_first) and result['reason'] == 'supported_turn'):
            return result['decision']
        return None

    def adopt_model_plan(self, state, decision, plan):
        if state.get('state_type') == 'map': self.route = start_route(state, decision)
        # Buying card removal opens a selection screen; keep the shopping list for afterwards.
        keep = plan and plan['kind'] == 'shop' and state.get('state_type') == 'card_select'
        plan = start_plan(state, decision) or (plan if keep else None)
        first = first_card(state, decision)
        if first and wastes_energy_card(state, first) and plan and plan['kind'] == 'turn':
            plan['expected'][first.get('name')] += 1  # It stays in hand instead of being played.
            decision = plan_decision(plan, state) or decision  # Skip the wasted energy card.
        return decision, plan

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
                if takeover: state = self.wait_until_settled(self.wait_for_play_phase(self.wait_for_teammates(self.wait_until_settled(state))))
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
                decision, plan = self.local_decision(state, plan, opened_cards, takeover, draining)
                if draining and decision is None: break
                asked_model = decision is None
                if asked_model:
                    if not self.allow_model:
                        self.report('程式無法可靠處理目前局面，已停止接手。\n此模式不會呼叫 AI，請手動選擇。')
                        break
                    if self.program_first and self.local_search:
                        self.report('程式需要 AI 判斷：' + self.local_search[1]['reason'])
                    decision = self.decide_routed(state)
                    decision, plan = self.adopt_model_plan(state, decision, plan)
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
