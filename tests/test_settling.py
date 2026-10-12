import copy
import unittest
from unittest.mock import patch
from takeover import controller as engine
from test_choices import sample


class SettlingTests(unittest.TestCase):
    def test_finished_queue_accepts_draw_replacing_played_card_with_identical_copy(self):
        before = sample()
        done = copy.deepcopy(before)
        done['player']['energy'] += 2
        done['player']['hp'] -= 6
        done['execution'] = {'queue_empty': True, 'running': False}
        c = engine.Controller(lambda m: None)
        with patch.object(engine.api, 'request', return_value=done) as read:
            c.wait_for_change(before, body={'action': 'play_card', 'card_index': 1})
        self.assertEqual(read.call_count, 1)

    def test_queue_busy_survives_long_quiet_gap_until_late_draw_and_energy(self):
        before = sample()
        removed = copy.deepcopy(before); removed['player']['hand'].pop(1)
        removed['execution'] = {'queue_empty': False, 'running': True}
        done = copy.deepcopy(removed)
        done['player']['hand'].append({'id': 'DRAWN', 'cost': '1', 'index': 2})
        done['player']['energy'] += 2
        done['execution'] = {'queue_empty': True, 'running': False}
        clock = [0]
        def wait(seconds): clock[0] += seconds; return False
        c = engine.Controller(lambda m: None)
        with patch.object(c.stop_event, 'wait', side_effect=wait), \
             patch.object(engine.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(engine.api, 'request', side_effect=[removed] * 12 + [done]) as read:
            c.wait_for_change(before, body={'action': 'play_card', 'card_index': 1})
        self.assertEqual(read.call_count, 13)

    def test_choice_prompt_returns_while_queue_is_paused(self):
        before = sample()
        choice = copy.deepcopy(before)
        choice['state_type'] = 'hand_select'
        choice['execution'] = {'queue_empty': False, 'running': False}
        clock = [0]
        def wait(seconds): clock[0] += seconds; return False
        c = engine.Controller(lambda m: None)
        with patch.object(c.stop_event, 'wait', side_effect=wait), \
             patch.object(engine.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(engine.api, 'request', return_value=choice) as read:
            c.wait_for_change(before, body={'action': 'play_card', 'card_index': 1})
        self.assertLess(read.call_count, 4)

    def test_busy_queue_timeout_does_not_allow_next_action(self):
        before = sample()
        busy = copy.deepcopy(before); busy['player']['hand'].pop(1)
        busy['execution'] = {'queue_empty': False, 'running': True}
        clock = [0]
        def wait(seconds): clock[0] += seconds; return False
        c = engine.Controller(lambda m: None)
        with patch.object(c.stop_event, 'wait', side_effect=wait), \
             patch.object(engine.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(engine.api, 'request', return_value=busy):
            with self.assertRaisesRegex(RuntimeError, '佇列'):
                c.wait_for_change(before, timeout=2, body={'action': 'play_card', 'card_index': 1})

    def test_execution_telemetry_does_not_change_decision_fingerprint(self):
        before = sample()
        busy = copy.deepcopy(before); busy['execution'] = {'queue_empty': False, 'running': True}
        self.assertEqual(engine.fingerprint(before), engine.fingerprint(busy))

    def test_mummified_hand_waits_for_delayed_discount(self):
        before = sample()
        before['player']['relics'] = [{'id': 'MUMMIFIED_HAND'}]
        before['player']['hand'][1]['type'] = 'Power'
        before['player']['hand'][0]['cost'] = '1'
        removed = copy.deepcopy(before); removed['player']['hand'].pop(1)
        done = copy.deepcopy(removed); done['player']['hand'][0]['cost'] = '0'
        states = [removed] * 12 + [done] * 8
        clock = [0]
        def wait(seconds): clock[0] += seconds; return False
        c = engine.Controller(lambda m: None)
        with patch.object(c.stop_event, 'wait', side_effect=wait), \
             patch.object(engine.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(engine.api, 'request', side_effect=states) as read:
            c.wait_for_change(before, body={'action': 'play_card', 'card_index': 1})
        self.assertGreater(read.call_count, 12)
        self.assertGreaterEqual(clock[0], 1.2 + engine.CARD_SETTLE_WINDOW)

    def test_mummified_hand_missing_discount_stops_safely(self):
        before = sample()
        before['player']['relics'] = [{'id': 'MUMMIFIED_HAND'}]
        before['player']['hand'][1]['type'] = 'Power'
        before['player']['hand'][0]['cost'] = '1'
        removed = copy.deepcopy(before); removed['player']['hand'].pop(1)
        clock = [0]
        def wait(seconds): clock[0] += seconds; return False
        c = engine.Controller(lambda m: None)
        with patch.object(c.stop_event, 'wait', side_effect=wait), \
             patch.object(engine.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(engine.api, 'request', return_value=removed):
            with self.assertRaisesRegex(RuntimeError, '干癟之手'):
                c.wait_for_change(before, timeout=2, body={'action': 'play_card', 'card_index': 1})

    def test_energy_spent_before_hand_and_block_settle_does_not_finish_wait(self):
        before = sample()
        spent = copy.deepcopy(before); spent['player']['energy'] -= 1
        for card in spent['player']['hand']: card['can_play'] = False
        done = copy.deepcopy(spent); done['player']['hand'].pop(1); done['player']['block'] = 5
        states = [spent] * 8 + [done] * 8
        clock = [0]
        def wait(seconds): clock[0] += seconds; return False
        c = engine.Controller(lambda m: None)
        with patch.object(c.stop_event, 'wait', side_effect=wait), \
             patch.object(engine.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(engine.api, 'request', side_effect=states) as read:
            c.wait_for_change(before, body={'action': 'play_card', 'card_index': 1})
        self.assertGreater(read.call_count, 8)  # Never accepts the energy-only snapshots.
        self.assertGreaterEqual(clock[0], .8 + engine.CARD_SETTLE_WINDOW)

    def test_resolved_card_has_no_fixed_half_second_pause(self):
        before = sample()
        done = copy.deepcopy(before)
        done['player']['hand'].pop(1)
        done['player']['block'] = 5
        clock = [0]
        def wait(seconds): clock[0] += seconds; return False
        c = engine.Controller(lambda m: None)
        with patch.object(c.stop_event, 'wait', side_effect=wait), \
             patch.object(engine.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(engine.api, 'request', return_value=done):
            c.wait_for_change(before, body={'action': 'play_card', 'card_index': 1})
        self.assertLess(clock[0], .5)

    def test_mummified_hand_with_only_free_cards_keeps_fast_wait(self):
        before = sample()
        before['player']['relics'] = [{'id': 'MUMMIFIED_HAND'}]
        before['player']['hand'][1]['type'] = 'Power'
        for card in before['player']['hand']: card['cost'] = '0'; card['star_cost'] = 0
        done = copy.deepcopy(before); done['player']['hand'].pop(1)
        clock = [0]
        def wait(seconds): clock[0] += seconds; return False
        c = engine.Controller(lambda m: None)
        with patch.object(c.stop_event, 'wait', side_effect=wait), \
             patch.object(engine.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(engine.api, 'request', return_value=done):
            c.wait_for_change(before, body={'action': 'play_card', 'card_index': 1})
        self.assertLess(clock[0], .5)

    def test_late_effect_restarts_card_quiet_window(self):
        before = sample()
        removed = copy.deepcopy(before); removed['player']['hand'].pop(1)
        done = copy.deepcopy(removed); done['player']['block'] = 5
        states = [removed, done] + [done] * 8
        clock = [0]
        def wait(seconds): clock[0] += seconds; return False
        c = engine.Controller(lambda m: None)
        with patch.object(c.stop_event, 'wait', side_effect=wait), \
             patch.object(engine.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(engine.api, 'request', side_effect=states):
            c.wait_for_change(before, body={'action': 'play_card', 'card_index': 1})
        self.assertGreaterEqual(clock[0], .1 + engine.CARD_SETTLE_WINDOW)

    def test_unresolved_card_times_out_without_allowing_another_action(self):
        before = sample()
        spent = copy.deepcopy(before); spent['player']['energy'] -= 1
        clock = [0]
        def wait(seconds): clock[0] += seconds; return False
        c = engine.Controller(lambda m: None)
        with patch.object(c.stop_event, 'wait', side_effect=wait), \
             patch.object(engine.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(engine.api, 'request', return_value=spent):
            with self.assertRaisesRegex(RuntimeError, '結算'):
                c.wait_for_change(before, timeout=2, body={'action': 'play_card', 'card_index': 1})
