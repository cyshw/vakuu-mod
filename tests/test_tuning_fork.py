import copy
import json
import unittest

from test_choices import sample
from test_search import card, enemy, state
from vakuu_choices import choice_offer, chosen_decision
from vakuu_plans import start_plan, plan_decision
from vakuu_relics import after_card_relics
from vakuu_search import search_turn


def fork(counter):
    return {'id': 'TUNING_FORK', 'counter': counter,
            'description': '你每打出10张技能牌，获得7点格挡。'}


class TuningForkTests(unittest.TestCase):
    def test_tenth_skill_adds_relic_block_but_attack_does_not(self):
        for kind, expected in [('Skill', 0), ('Attack', 7)]:
            c = dict(card(0, block=5) if kind == 'Skill' else card(0), type=kind)
            s = state([c], [enemy(hp=50, attack=7)], energy=1)
            s['player']['relics'] = [fork(9)]
            self.assertEqual(search_turn(s)['predicted_hp_loss'], expected)

    def test_counter_wraps_once_across_two_skills(self):
        s = state([dict(card(i, block=1), type='Skill') for i in range(2)],
                  [enemy(hp=50, attack=10)], energy=2)
        s['player']['relics'] = [fork(8)]
        self.assertEqual(search_turn(s)['predicted_hp_loss'], 1)  # 2 card block + 7 relic block.
        self.assertEqual(after_card_relics(after_card_relics([fork(8)], {'type': 'Skill'}), {'type': 'Skill'}), [fork(0)])

    def test_unknown_counter_or_modified_effect_keeps_full_state_fallback(self):
        for variant in (None, -1, 10, True, '9'):
            s = sample(); s['player']['relics'] = [fork(variant)]
            self.assertIsNone(choice_offer(s))
        s = sample(); s['player']['relics'] = [fork(9)]
        s['player']['relics'][0]['description'] = '你每打出10张技能牌，获得14点格挡。'
        self.assertIsNone(choice_offer(s))

    def test_queue_accepts_predicted_counter_shift_and_rejects_other_changes(self):
        s = sample(); s['player']['relics'] = [fork(9)]
        offer = {'menu': {'plans': [{'id': 'P1', 'sequence': [{'card_index': 1}, {'card_index': 0, 'target': 'a'}],
                                   'confidence': 'partial', 'end_turn': False}]}}
        decision = chosen_decision(s, offer, {'choice': 'P1', 'reject_reason': '', 'reason': '先格擋'})
        after = copy.deepcopy(s)
        after['player']['hand'].pop(1)
        for i, c in enumerate(after['player']['hand']): c['index'] = i
        after['player']['energy'] -= 1
        after['player']['relics'][0]['counter'] = 0
        self.assertEqual(json.loads(plan_decision(start_plan(s, decision), after)['parameters_json']),
                         {'card_index': 0, 'target': 'a'})
        after['player']['relics'][0]['counter'] = 1
        self.assertIsNone(plan_decision(start_plan(s, decision), after))
        self.assertEqual(s['player']['relics'][0]['counter'], 9)

    def test_live_held_cards_and_inert_statuses_allow_partial_offer(self):
        s = sample(); s['player']['relics'] = [fork(0)]
        s['battle']['enemies'][0]['status'] = [{'id': 'ARTIFACT_POWER', 'amount': 1}]
        s['player']['status'] = [{'id': 'LIGHTNING_ROD_POWER', 'amount': 1}]
        s['player']['hand'].append({'id': 'HOLOGRAM', 'index': 3, 'type': 'Skill', 'cost': '1',
                                   'target_type': 'Self', 'can_play': True,
                                   'description': '获得3点格挡。 将弃牌堆中的一张牌放入你的手牌。 消耗。'})
        self.assertIsNotNone(choice_offer(s))
        self.assertEqual(search_turn(s)['reason'], 'unsupported_hand')

    def test_next_turn_energy_and_enemy_ritual_do_not_trigger_during_supported_plays(self):
        s = sample()
        s['player']['status'] = [{'id': 'ENERGY_NEXT_TURN_POWER', 'amount': 1}, {'id': 'STORM_POWER', 'amount': 1}]
        s['battle']['enemies'][0]['status'] = [{'id': 'RITUAL_POWER', 'amount': 6}]
        self.assertIsNotNone(choice_offer(s))

    def test_held_helix_display_change_invalidates_selected_queue(self):
        s = sample()
        s['player']['hand'].append({'id': 'HELIX_DRILL', 'name': '螺旋钻击', 'index': 3, 'type': 'Attack',
            'cost': '0', 'can_play': True, 'target_type': 'AnyEnemy',
            'description': '在本回合中，这张牌以外使用的每个[defect_energy_icon.png]，都会使此牌造成3点伤害一次。 （命中0次）'})
        self.assertIsNotNone(choice_offer(s))
        offer = {'menu': {'plans': [{'id': 'P1', 'sequence': [{'card_index': 1}, {'card_index': 0, 'target': 'a'}],
                                   'confidence': 'partial', 'end_turn': False}]}}
        decision = chosen_decision(s, offer, {'choice': 'P1', 'reject_reason': '', 'reason': '起手'})
        after = copy.deepcopy(s)
        after['player']['hand'].pop(1)
        for i, c in enumerate(after['player']['hand']): c['index'] = i
        after['player']['energy'] -= 1
        after['player']['hand'][-1]['description'] = after['player']['hand'][-1]['description'].replace('命中0次', '命中1次')
        self.assertIsNone(plan_decision(start_plan(s, decision), after))
