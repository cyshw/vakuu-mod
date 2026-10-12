import copy
import json
from pathlib import Path
import unittest

from combat.facts import combat_facts
from combat.relics import CONDITIONAL_RELICS, RELIC_RULES
from combat.search import evaluate_sequence, search_turn
from combat.triggers import compact_triggers, card_trigger_values
from test_search import card, enemy, state


def powers(after=1, exhaust=3, accuracy=6):
    return [{'id': 'AFTERIMAGE_POWER', 'amount': after, 'description': f'你每打出一张牌，都获得{after}点格挡。'},
            {'id': 'FEEL_NO_PAIN_POWER', 'amount': exhaust, 'description': f'每当有一张牌被消耗时，获得{exhaust}点格挡。'},
            {'id': 'ACCURACY_POWER', 'amount': accuracy, 'description': f'小刀造成{accuracy}点额外伤害。'}]


def shiv(index=0, damage=10):
    return dict(card(index, damage=damage, cost=0), id='SHIV', name='小刀', type='Attack',
                description=f'造成{damage}点伤害。消耗。')


class OnPlayBlockTests(unittest.TestCase):
    def test_recorded_shiv_block_delta_matches_known_triggers(self):
        sample = json.loads((Path(__file__).parent / 'fixtures/onplay-block-recorded.json').read_text(encoding='utf-8'))
        values = card_trigger_values({'status': sample['status']})
        self.assertEqual(sample['block_after'] - sample['block_before'],
                         values['block_per_card'] + values['block_per_exhaust'])

    def test_shiv_with_accuracy_is_not_buffed_twice_and_block_is_unpowered(self):
        s = state([shiv(), dict(card(1, block=3), type='Skill')], [enemy(hp=99, attack=10)], energy=1)
        s['player']['status'] = powers() + [{'id': 'FRAIL_POWER', 'amount': 1}]
        result = evaluate_sequence(s, [{'card_index': 0, 'target': 'a'}, {'card_index': 1}])
        self.assertEqual(result['predicted_enemy_hp'], [89])
        self.assertEqual(result['block_gained'], 8)  # 3 displayed Defend + 2 Afterimage + 3 exhaust.
        self.assertEqual(result['predicted_hp_loss'], 2)

    def test_inert_ethereal_exhaust_after_clasp_and_orbs_adds_block(self):
        dazed = {'id': 'DAZED', 'description': '不能被打出。虚无。', 'can_play': False}
        s = state([shiv(), dazed], [enemy(hp=99, attack=10)], energy=0)
        s['player']['status'] = powers()
        s['player']['relics'] = [{'id': 'CLOAK_CLASP', 'description': CONDITIONAL_RELICS['CLOAK_CLASP'], 'counter': None}]
        result = evaluate_sequence(s, [{'card_index': 0, 'target': 'a'}])
        self.assertEqual(result['end_turn_relic_block'], 1)  # Dazed still in hand when Clasp counts.
        self.assertEqual(result['end_turn_exhaust_block'], 3)
        self.assertEqual(result['predicted_hp_loss'], 2)
        self.assertEqual(combat_facts(s)['block_needed_for_no_damage'], 5)  # End now: 2 Clasp + 3 exhaust.

    def test_panache_and_tuning_fork_stack_without_counting_potions(self):
        s = state([shiv(), dict(card(1, block=2), type='Skill')], [enemy(hp=99, attack=15)], energy=1)
        s['player']['status'] = powers() + [{'id': 'PANACHE_POWER', 'amount': 2,
            'description': '如果你在本回合再打出2张牌，就对所有敌人造成10点伤害。'}]
        s['player']['relics'] = [{'id': 'TUNING_FORK', 'counter': 9, 'description': RELIC_RULES['TUNING_FORK'].description}]
        result = evaluate_sequence(s, [{'card_index': 0, 'target': 'a'}, {'card_index': 1}])
        self.assertEqual(result['predicted_enemy_hp'], [79])
        self.assertEqual(result['block_gained'], 14)
        self.assertEqual(result['predicted_hp_loss'], 1)
        self.assertEqual(result['panache_cards_left'], [5])

    def test_unknown_exhaust_draw_and_modified_power_still_decline(self):
        s = state([shiv()], [enemy(hp=99)], energy=0)
        s['player']['status'] = powers() + [{'id': 'DARK_EMBRACE_POWER', 'amount': 1}]
        self.assertEqual(search_turn(s)['reason'], 'unknown_player_status')
        s['player']['status'] = powers()
        s['player']['status'][0]['description'] += '抽1张牌。'
        self.assertEqual(search_turn(s)['reason'], 'unknown_card_trigger')
        payload = copy.deepcopy(s); payload['combat_facts'] = combat_facts(s)
        compact_triggers(payload, s)
        self.assertTrue(any(p['id'] == 'AFTERIMAGE_POWER' for p in payload['player']['status']))

    def test_numeric_summary_replaces_only_validated_power_prose(self):
        s = state([shiv()]); s['player']['status'] = powers()
        before = copy.deepcopy(s)
        payload = copy.deepcopy(s); payload['combat_facts'] = combat_facts(s)
        compact_triggers(payload, s)
        self.assertEqual(payload['player']['status'], [])
        self.assertEqual(len(payload['combat_facts']['card_trigger_counts']), 3)
        self.assertEqual(s, before)

    def test_all_enemy_shiv_and_added_onplay_effect_are_not_implicitly_supported(self):
        s = state([dict(shiv(), target_type='AllEnemies')])
        self.assertIsNone(search_turn(s)['decision'])
        s['player']['hand'] = [dict(shiv(), description='造成10点伤害。消耗。抽1张牌。')]
        self.assertIsNone(search_turn(s)['decision'])
