import copy
import unittest
from unittest.mock import patch
import vakuu_engine as engine
from test_choices import sample


class SettlingTests(unittest.TestCase):
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
