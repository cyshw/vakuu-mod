import copy
import unittest

from combat.card_effects import STRIKES, DEFENDS, card_move
from combat.relics import RELIC_RULES, after_card_relics, tuning_fork_counter
from combat.search import evaluate_sequence
from test_search import card, enemy, state


class EffectRegistryTests(unittest.TestCase):
    def test_each_character_starter_uses_the_same_supported_effect(self):
        for cid in STRIKES:
            self.assertEqual(card_move(dict(card(0, damage=9), id=cid)).damage, 9)
        for cid in DEFENDS:
            self.assertEqual(card_move(dict(card(0, block=8), id=cid)).block, 8)

    def test_registered_id_alone_never_accepts_added_effects_or_wrong_target(self):
        s = card(0)
        for altered in (dict(s, description=s['description'] + '抽1张牌。'),
                        dict(s, target_type='Self'), dict(s, star_cost=1),
                        dict(s, index=True), dict(s, cost='X'), dict(s, id='UNREGISTERED')):
            self.assertIsNone(card_move(altered))

    def test_ordered_damage_then_vulnerable_and_each_hit_remain_distinct(self):
        bash = dict(card(0, cost=2), id='BASH', description='造成8点伤害。给予2层易伤。')
        twin = dict(card(1), id='TWIN_STRIKE', description='造成7点伤害两次。')
        s = state([bash, twin], [enemy(hp=29, block=1)], energy=3)
        result = evaluate_sequence(s, [{'card_index': 0, 'target': 'a'}, {'card_index': 1, 'target': 'a'}])
        self.assertEqual(result['predicted_enemy_hp'], [2])  # 8-1 + floor(7*1.5)*2.

    def test_unsupported_future_benefit_stays_kill_only(self):
        c = dict(card(0), id='MOMENTUM_STRIKE',
                 description='造成9点伤害。这张牌的耗能降为0[defect_energy_icon.png]。')
        self.assertTrue(card_move(c).kill_only)
        result = evaluate_sequence(state([c], [enemy(hp=99)]), [{'card_index': 0, 'target': 'a'}])
        self.assertEqual(result['confidence'], 'partial')

    def test_counter_trigger_ignores_attack_and_does_not_mutate_original(self):
        fork = {'id': 'TUNING_FORK', 'description': RELIC_RULES['TUNING_FORK'].description, 'counter': 9}
        before = copy.deepcopy(fork)
        self.assertEqual(tuning_fork_counter(after_card_relics([fork], {'type': 'Attack'})), 9)
        self.assertEqual(tuning_fork_counter(after_card_relics([fork], {'type': 'Skill'})), 0)
        self.assertEqual(fork, before)
        s = state([dict(card(0, block=1), type='Skill')], [enemy(hp=99, attack=8)], energy=1)
        s['player']['relics'] = [fork]
        self.assertEqual(evaluate_sequence(s, [{'card_index': 0}])['predicted_hp_loss'], 0)

