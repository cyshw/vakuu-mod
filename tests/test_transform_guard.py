import copy
import unittest
from core.rules import still_valid
from combat.search import search_turn
from test_search import card, enemy, state


class TransformGuardTests(unittest.TestCase):
    def test_pickup_only_new_leaf_does_not_block_combat_search(self):
        s = state([card(0, damage=6)], [enemy(hp=6)])
        s['player']['relics'] = [{'id': 'NEW_LEAF', 'description': '拾起时，变化1张牌。'}]
        self.assertIsNotNone(search_turn(s)['decision'])

    def test_random_preview_does_not_stop_confirm_but_other_changes_do(self):
        before = {'state_type': 'card_select', 'card_select': {
            'screen_type': 'transform', 'preview_showing': True, 'can_confirm': True,
            'selected_count': 1, 'cards': [{'id': 'STRIKE', 'index': 0, 'selected': True}],
            'preview_cards': [{'id': 'STRIKE'}, {'id': 'CHAOS'}]}}
        now = copy.deepcopy(before)
        now['card_select']['preview_cards'][1]['id'] = 'SHADOW_SHIELD'
        body = {'action': 'confirm_selection'}
        self.assertTrue(still_valid(before, now, body))
        for field, value in (('can_confirm', False), ('selected_count', 0), ('screen_type', 'upgrade'),
                             ('cards', [{'id': 'DEFEND', 'index': 0, 'selected': True}])):
            changed = copy.deepcopy(now); changed['card_select'][field] = value
            with self.subTest(field): self.assertFalse(still_valid(before, changed, body))
        self.assertFalse(still_valid(before, now, {'action': 'select_card', 'index': 0}))
        changed = copy.deepcopy(now); changed['run'] = {'floor': 2}
        self.assertFalse(still_valid(before, changed, body))
