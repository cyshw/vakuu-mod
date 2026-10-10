import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'outputs'))
from sts2_advisor import advise

def card(key, name, index=0, upgraded=False):
    return dict(id=key, name=name, index=index, is_upgraded=upgraded)

def sample():
    return {
        'state_type': 'card_reward',
        'player': {
            'character_id': 'IRONCLAD', 'character': '鐵甲戰士',
            'deck': [card('CORRUPTION', '腐化'), card('FEEL_NO_PAIN', '無懼疼痛'),
                     card('STRIKE_IRONCLAD', '打擊'), card('STRIKE_IRONCLAD', '打擊')],
            'relics': [{'id': 'BURNING_BLOOD', 'name': '燃燒之血'}],
        },
        'card_reward': {'can_skip': True, 'cards': [
            card('DARK_EMBRACE', '黑暗之擁', 2),
            card('BODY_SLAM', '全身撞擊', 7),
            card('BODY_SLAM', '全身撞擊+', 9, True)]}
    }

class AdapterTests(unittest.TestCase):
    def test_shop_cards_are_scored_with_shop_indices(self):
        state = sample()
        state['state_type'] = 'shop'
        state['shop'] = {'items': [
            {'index': 0, 'category': 'card', 'is_stocked': True, 'card_id': 'DARK_EMBRACE', 'card_name': '黑暗之擁'},
            {'index': 1, 'category': 'card', 'is_stocked': False, 'card_id': 'BODY_SLAM', 'card_name': '全身撞擊'},
            {'index': 5, 'category': 'relic', 'is_stocked': True, 'relic_name': '遺物'},
            {'index': 6, 'category': 'card', 'is_stocked': True, 'card_id': 'BODY_SLAM', 'card_name': '全身撞擊'}]}
        result = advise(state)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(sorted(c['index'] for c in result['ranked_cards']), [0, 6])  # Sold-out and relics skipped.

    def test_localized_ids_duplicates_upgrades_and_indices(self):
        state = sample()
        original = copy.deepcopy(state)
        result = advise(state)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['deck_count'], 4)
        self.assertEqual(result['archetype'], 'exhaust')
        rows = {r['index']: r for r in result['ranked_cards']}
        self.assertEqual(set(rows), {2, 7, 9})
        self.assertEqual(rows[2]['english_name'], 'Dark Embrace')
        self.assertEqual(rows[9]['english_name'], 'Body Slam+')
        self.assertGreater(rows[9]['score'], rows[7]['score'])
        self.assertEqual(rows[9]['name'], '全身撞擊+')
        self.assertEqual(state, original)
        self.assertFalse(result['skip_evaluated'])

    def test_missing_deck_does_not_guess(self):
        state = sample()
        del state['player']['deck']
        self.assertEqual(advise(state)['status'], 'missing_deck_api')

    def test_unknown_mod_card_disables_best_pick(self):
        state = sample()
        state['card_reward']['cards'].append(card('MOD_UNKNOWN_CARD', '模組牌', 4))
        result = advise(state)
        self.assertEqual(result['status'], 'incomplete_data')
        self.assertIsNone(result['best_scored_index'])
        self.assertEqual(result['unknown_items'][0]['id'], 'MOD_UNKNOWN_CARD')

    def test_menu_never_scores(self):
        self.assertEqual(advise({'state_type': 'menu'})['status'], 'not_card_reward')

if __name__ == '__main__':
    unittest.main()
