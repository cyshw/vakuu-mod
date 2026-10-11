import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import spire_session as transport
import vakuu_engine as engine
from test_choices import sample

SELECTION = {'model_key': 'codex:gpt-6.1-sol', 'effort': 'low'}


def bare_session():
    session = transport.CombatSession.__new__(transport.CombatSession)
    session.selection = SELECTION
    session.stop_event = threading.Event()
    session.closed = threading.Event()
    session.messages = queue.Queue()
    session.pending = []
    session.thread_id = None
    session.initialized = False
    session.request_id = session.turns = 0
    session.process = Mock()
    session.directory = Path.cwd()
    return session


def turn_events(session, turn_id, answer):
    session.messages.put({'method': 'thread/tokenUsage/updated', 'params': {
        'threadId': 'fight', 'turnId': turn_id, 'tokenUsage': {
            'last': {'inputTokens': 200, 'cachedInputTokens': 150, 'outputTokens': 10},
            'total': {'inputTokens': 400, 'cachedInputTokens': 150, 'outputTokens': 20}}}})
    session.messages.put({'method': 'item/completed', 'params': {
        'threadId': 'fight', 'turnId': turn_id,
        'item': {'type': 'agentMessage', 'text': json.dumps(answer)}}})
    session.messages.put({'method': 'turn/completed', 'params': {
        'threadId': 'fight', 'turn': {'id': turn_id, 'status': 'completed'}}})


class TransportTests(unittest.TestCase):
    def test_same_thread_accepts_new_schema_and_uses_last_not_total_usage(self):
        s = bare_session()
        turn_events(s, 'one', {'choice': 'reject'})
        turn_events(s, 'two', {'action': 'end_turn'})
        with patch.object(s, '_rpc', side_effect=[{}, {'thread': {'id': 'fight'}},
                          {'turn': {'id': 'one'}}, {'turn': {'id': 'two'}}]) as rpc:
            self.assertEqual(s.ask('candidate', {'title': 'candidate'}), {'choice': 'reject'})
            self.assertEqual(s.ask('latest full hand', {'title': 'full'}), {'action': 'end_turn'})
        starts = [call for call in rpc.call_args_list if call.args[0] == 'turn/start']
        self.assertEqual([call.args[1]['threadId'] for call in starts], ['fight', 'fight'])
        self.assertEqual(starts[1].args[1]['outputSchema'], {'title': 'full'})
        self.assertIn('latest full hand', starts[1].args[1]['input'][0]['text'])
        self.assertEqual(rpc.call_args_list[1].args[1]['environments'], [])
        self.assertTrue(rpc.call_args_list[1].args[1]['ephemeral'])
        self.assertEqual(s.last_metrics['usage']['input_tokens'], 200)
        self.assertEqual(s.last_metrics['session_total_usage']['input_tokens'], 400)
        self.assertEqual(s.last_metrics['session_turn'], 2)

    def test_rpc_buffers_notifications_before_reply(self):
        s = bare_session()
        event = {'method': 'thread/started', 'params': {'threadId': 'fight'}}
        s.messages.put(event)
        s.messages.put({'id': 1, 'result': {'thread': {'id': 'fight'}}})
        import time
        self.assertEqual(s._rpc('thread/start', {}, time.monotonic() + 1), {'thread': {'id': 'fight'}})
        self.assertEqual(s.pending, [event])

    def test_stop_timeout_closed_process_and_tool_requests_fail_without_retry(self):
        import time
        for kind in ('stop', 'timeout', 'closed', 'tool'):
            s = bare_session()
            if kind == 'stop': s.stop_event.set()
            elif kind == 'closed': s.messages.put({'closed': True})
            elif kind == 'tool': s.messages.put({'id': 5, 'method': 'item/commandExecution/requestApproval'})
            with self.subTest(kind), self.assertRaises(InterruptedError if kind == 'stop' else RuntimeError):
                s._next(time.monotonic() + .01)
            if kind == 'tool': self.assertIn('error', json.loads(s.process.stdin.write.call_args.args[0]))

    def test_missing_usage_is_unknown(self):
        self.assertIsNone(transport.normalize_usage(None))
        self.assertIsNone(transport.normalize_usage({'inputTokens': -1}))


class LifecycleTests(unittest.TestCase):
    def test_reuse_followup_then_close_at_fight_end_or_restart(self):
        state = sample(); state['run'] = {'act': 1, 'floor': 4}
        answer = {'choice': 'reject', 'reject_reason': 'insufficient_information', 'reason': '完整手牌'}
        full = {'action': 'end_turn', 'parameters_json': '{}', 'stop': False, 'advice': '防禦'}
        c = engine.Controller(lambda m: None)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, patch.object(engine, 'ROOT', Path(directory)), \
             patch.object(engine, 'CombatSession') as factory, \
             patch.object(engine, 'monster_intel', return_value=None):
            session = factory.return_value
            session.selection = SELECTION
            session.directory = Path(directory)
            session.last_metrics = {'usage': None}
            session.turns = 0
            session.ask.side_effect = [answer, full, answer, full]
            self.assertEqual(c.decide(state, SELECTION), full)
            state['battle']['round'] = 2
            self.assertEqual(c.decide(state, SELECTION), full)
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(session.ask.call_count, 4)
            selecting = {**state, 'state_type': 'hand_select'}
            c.sync_combat(selecting)
            self.assertIs(c.combat_session, session)
            grid = {**state, 'state_type': 'card_select', 'battle': None,
                    'card_select': {'screen_type': 'NCombatPileCardSelectScreen'}}
            c.sync_combat(grid)
            self.assertIs(c.combat_session, session)
            self.assertEqual(c.combat_round, 2)
            state['battle']['round'] = 1
            c.sync_combat(state)
            session.close.assert_called_once()
            self.assertIsNone(c.combat_session)
            c.combat_session = Mock()
            second = c.combat_session
            c.sync_combat({'state_type': 'card_reward'})
            second.close.assert_called_once()

    def test_shop_grid_with_no_combat_player_fields_closes_session(self):
        c = engine.Controller(lambda m: None)
        c.combat_key = (1, 4)
        c.combat_session = session = Mock()
        c.sync_combat({'state_type': 'card_select', 'run': {'act': 1, 'floor': 4},
                       'player': {'hp': 40, 'deck': []}})
        session.close.assert_called_once()
        self.assertIsNone(c.combat_key)

    def test_codex_keeps_battle_thread_after_eight_calls(self):
        c = engine.Controller(lambda m: None)
        c.combat_key = (1, 17)
        session = Mock(selection=SELECTION, turns=engine.SESSION_MAX_TURNS,
                       last_metrics={'usage': None})
        session.ask.return_value = {'action': 'end_turn'}
        c.combat_session = session
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, \
             patch.object(engine, 'ROOT', Path(directory)), patch.object(engine, 'prune_runs'), \
             patch.object(engine, 'CombatSession') as factory:
            c.call_model('latest state', {}, SELECTION)
        factory.assert_not_called()
        session.close.assert_not_called()
        session.ask.assert_called_once()

    def test_stop_model_change_and_failure_close_session(self):
        c = engine.Controller(lambda m: None)
        c.combat_session = Mock()
        first = c.combat_session
        c.stop()
        first.close.assert_called_once()
        c.stop_event.clear()
        c.combat_key = (1, 4)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, patch.object(engine, 'ROOT', Path(directory)), \
             patch.object(engine, 'CombatSession') as factory:
            old = Mock(selection={'model_key': 'codex:gpt-6-luna', 'effort': 'low'})
            c.combat_session = old
            factory.return_value.ask.side_effect = RuntimeError('quota')
            with self.assertRaisesRegex(RuntimeError, 'quota'):
                c.call_model('latest', {}, SELECTION)
            old.close.assert_called_once()
            factory.return_value.close.assert_called_once()
            self.assertIsNone(c.combat_session)
            factory.return_value.ask.assert_called_once()

    def test_local_winning_action_also_closes_conversation(self):
        state = sample(); state['run'] = {'act': 1, 'floor': 4}
        c = engine.Controller(lambda m: None)
        c.sync_combat(state)
        session = c.combat_session = Mock()
        decision = {'action': 'end_turn', 'parameters_json': '{}', 'stop': False, 'advice': '結束'}
        responses = [state, state, {'status': 'ok'}, {'state_type': 'card_reward'}]
        with patch.object(engine.api, 'request', side_effect=responses), \
             patch.object(engine, 'auto_decision', return_value=decision), patch.object(c, 'wait_for_change'):
            c.run(True, 1)
        session.close.assert_called_once()
        self.assertIsNone(c.combat_session)


CLAUDE = {'model_key': 'claude:sonnet', 'effort': 'low'}
ANSWER = {'advice': '打', 'stop': False, 'action': 'end_turn', 'parameters_json': '{}', 'next_cards': []}


class FakeClaudeSession:
    made = []

    def __init__(self, selection, directory, stop_event, schema, fail=False):
        self.selection, self.directory, self.schema = dict(selection), directory, schema
        self.turns, self.last_metrics, self.sent, self.closed = 0, None, [], False
        FakeClaudeSession.made.append(self)

    def ask(self, prompt, schema):
        self.sent.append(prompt); self.turns += 1
        self.last_metrics = {'transport': 'claude_session', 'session_turn': self.turns}
        return ANSWER

    def close(self): self.closed = True


class ClaudeSessionTests(unittest.TestCase):
    def setUp(self): FakeClaudeSession.made = []

    def test_one_claude_process_per_fight_and_rules_sent_once(self):
        c = engine.Controller(lambda m: None); c.combat_key = (1, 5)
        with patch.object(engine, 'ClaudeCombatSession', FakeClaudeSession), patch.object(engine, 'prune_runs'):
            c.call_model(engine.instructions() + '\n狀態一', engine.SCHEMA, CLAUDE)
            c.call_model(engine.instructions() + '\n狀態二', engine.SCHEMA, CLAUDE)
        self.assertEqual(len(FakeClaudeSession.made), 1)
        first, second = FakeClaudeSession.made[0].sent
        self.assertTrue(first.startswith(engine.instructions()))
        self.assertEqual(second, engine.REPEATED_RULES + '\n狀態二')
        c.combat_key = (1, 6); c.close_combat_session()
        self.assertTrue(FakeClaudeSession.made[0].closed)

    def test_long_fights_start_a_fresh_conversation_every_eight_calls(self):
        c = engine.Controller(lambda m: None); c.combat_key = (1, 17)
        with patch.object(engine, 'ClaudeCombatSession', FakeClaudeSession), patch.object(engine, 'prune_runs'):
            for turn in range(engine.SESSION_MAX_TURNS + 1):
                c.call_model(engine.instructions() + f'\n回合{turn}', engine.SCHEMA, CLAUDE)
        first, second = FakeClaudeSession.made
        self.assertEqual(len(first.sent), engine.SESSION_MAX_TURNS)
        self.assertTrue(first.closed)
        self.assertTrue(second.sent[0].startswith(engine.instructions()))  # The new conversation gets the rules again.

    def test_broken_claude_session_falls_back_to_a_full_one_off_call(self):
        class Broken(FakeClaudeSession):
            def ask(self, prompt, schema): raise RuntimeError('pipe closed')
        c = engine.Controller(lambda m: None); c.combat_key = (1, 5)
        process = Mock(returncode=0); process.communicate.return_value = ('{}', '')
        with patch.object(engine, 'ClaudeCombatSession', Broken), patch.object(engine, 'prune_runs'), \
             patch.object(engine.subprocess, 'Popen', return_value=process), \
             patch.object(engine.models, 'parse_answer', return_value=ANSWER):
            self.assertEqual(c.call_model(engine.instructions() + '\n狀態', engine.SCHEMA, CLAUDE), ANSWER)
        self.assertIsNone(c.combat_session)
        self.assertTrue(process.communicate.call_args.args[0].startswith(engine.instructions()))

    def test_claude_outside_combat_stays_one_off(self):
        c = engine.Controller(lambda m: None); c.combat_key = None
        process = Mock(returncode=0); process.communicate.return_value = ('{}', '')
        with patch.object(engine, 'ClaudeCombatSession', FakeClaudeSession), patch.object(engine, 'prune_runs'), \
             patch.object(engine.subprocess, 'Popen', return_value=process), \
             patch.object(engine.models, 'parse_answer', return_value=ANSWER):
            c.call_model('prompt', engine.SCHEMA, CLAUDE)
        self.assertEqual(FakeClaudeSession.made, [])


class AgySessionTests(unittest.TestCase):
    def test_turn_usage_is_the_difference_of_cumulative_totals(self):
        s = transport.AgyCombatSession.__new__(transport.AgyCombatSession)
        s.previous_usage = {}; s.turns = 1
        answer = {'advice': '打', 'stop': False, 'action': 'end_turn', 'parameters_json': '{}', 'next_cards': []}
        event = lambda total: {'event': 'result', 'result': {'status': 'SUCCESS', 'structured_output': answer,
                                                             'usage': {'input_tokens': total, 'output_tokens': 10}}}
        self.assertEqual(s._answer(event(20000)), answer)
        s.turns = 2
        s._answer(event(24000))
        self.assertEqual(s.last_metrics['usage'], {'input_tokens': 4000, 'output_tokens': 0})
        self.assertEqual(json.loads(s._message('hi')), {'event': 'user', 'message': {'content': 'hi'}})
