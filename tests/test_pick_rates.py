import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vakuu'))
from deck import pick_rates
from takeover import controller

TABLE = {'characters': {'IRONCLAD': {
    '1': {'TWIN_STRIKE': [100, 60], 'STOMP': [100, 10], 'ARMAMENTS': [100, 30]},
    '2': {'TWIN_STRIKE': [100, 20], 'STOMP': [100, 5], 'ARMAMENTS': [100, 15]}}}}


def reward(act, cards=('STOMP', 'TWIN_STRIKE', 'ARMAMENTS'), can_skip=True):
    return {'state_type': 'card_reward', 'run': {'act': act}, 'player': {'character_id': 'IRONCLAD'},
            'card_reward': {'can_skip': can_skip, 'cards': [{'index': i, 'id': c, 'name': c} for i, c in enumerate(cards)]}}


class PickRateTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(pick_rates, '_table', TABLE)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_act_1_takes_the_most_taken_card(self):
        self.assertEqual(pick_rates.popular_pick(reward(1))['index'], 1)

    def test_later_acts_skip_cards_players_rarely_take(self):
        self.assertTrue(pick_rates.popular_pick(reward(2))['skip'])
        self.assertEqual(pick_rates.popular_pick(reward(2, can_skip=False))['index'], 1)

    def test_no_table_for_the_character_falls_back(self):
        state = reward(1); state['player']['character_id'] = 'REGENT'
        self.assertIsNone(pick_rates.popular_pick(state))

    def test_takeover_moves_without_the_model(self):
        c = controller.Controller(lambda m: None)
        with patch.object(controller, 'CARD_PICK', 'top'), patch.object(c, 'call_model') as call:
            take = c.decide(reward(1), {'model_key': 'claude:haiku', 'effort': 'low'})
            skip = c.decide(reward(2), {'model_key': 'claude:haiku', 'effort': 'low'})
        call.assert_not_called()
        self.assertEqual((take['action'], json.loads(take['parameters_json'])), ('select_card_reward', {'card_index': 1}))
        self.assertEqual(skip['action'], 'skip_card_reward')


if __name__ == '__main__':
    unittest.main()
