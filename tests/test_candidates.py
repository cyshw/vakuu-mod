import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'vakuu'))
from vakuu_search import search_turn, turn_candidates
import vakuu_engine as engine
from test_search import card, enemy, state


class CandidateTests(unittest.TestCase):
    def test_search_provides_distinct_outcomes_with_real_indices(self):
        s = state([card(7, damage=10), card(12, block=8)], [enemy(hp=30)], energy=1)
        result = search_turn(s, include_candidates=True)
        plans = turn_candidates(s, result)['plans']
        calculated = [p for p in plans if p['confidence'] == 'simulated']
        self.assertGreaterEqual(len(calculated), 2)
        signatures = {(tuple(p['predicted_enemy_hp']), p['predicted_hp_loss'], p['remaining_energy'])
                      for p in calculated}
        self.assertEqual(len(calculated), len(signatures))
        self.assertTrue(all(a['card_index'] in {7, 12} for p in plans for a in p['sequence']))

    def test_partial_forecasts_do_not_claim_to_include_burn(self):
        s = state([card(7, damage=10), {'id': 'BURN', 'can_play': False,
                   'description': '不能被打出。 在你的回合结束时，如果这张牌在你的手牌中，你受到2点伤害。'}],
                  [enemy(hp=30)])
        result = search_turn(s, include_candidates=True)
        self.assertIsNone(result['decision'])
        self.assertTrue(result['candidates'])
        self.assertTrue(all(p['confidence'] == 'partial' and p['unmodeled_effects']
                            for p in result['candidates']))

    def test_unknown_triggers_get_no_fabricated_forecasts(self):
        s = state([dict(card(2), type='Attack'), dict(card(9, block=5), type='Skill')])
        s['battle']['enemies'][0]['status'] = [{'id': 'THORNS_POWER'}]
        plans = turn_candidates(s)['plans']
        self.assertTrue(plans)
        for plan in plans:
            self.assertEqual(plan['confidence'], 'unverified')
            self.assertNotIn('predicted_hp_loss', plan)
            self.assertNotIn('predicted_enemy_hp', plan)
            self.assertFalse(plan['end_turn'])

    def test_draw_and_power_proposals_stop_at_uncertain_effect(self):
        s = state([{'id': 'COOLHEADED', 'name': '冷靜頭腦', 'type': 'Skill', 'index': 11,
                    'cost': '1', 'can_play': True, 'target_type': 'Self',
                    'description': '生成1个冰霜充能球。 抽1张牌。'},
                   {'id': 'ECHO_FORM', 'type': 'Power', 'index': 8, 'cost': '3', 'can_play': True,
                    'target_type': 'Self', 'description': '你每回合打出的第一张牌会被打出两次。'},
                   dict(card(5), type='Attack')], energy=3)
        s['battle']['enemies'][0]['status'] = [{'id': 'THORNS_POWER'}]
        plans = turn_candidates(s)['plans']
        draws = [p for p in plans if p['style'] == '抽牌後重算']
        powers = [p for p in plans if p['style'] == '能力牌起手']
        self.assertEqual(draws[0]['sequence'], [{'card_index': 11}])
        self.assertEqual(powers[0]['sequence'], [{'card_index': 8}])
        self.assertTrue(draws[0]['requires_replan'])
        self.assertFalse(draws[0]['end_turn'])

    def test_heuristic_sequence_respects_budget_and_excludes_x_and_stars(self):
        s = state([dict(card(3, cost=1), type='Attack'), dict(card(9, cost=2), type='Attack'),
                   dict(card(4, cost=0), type='Attack', star_cost=1),
                   dict(card(7), type='Attack', cost='X')], energy=2)
        s['player']['status'] = [{'id': 'UNKNOWN_POWER'}]
        for plan in turn_candidates(s)['plans']:
            indices = [a['card_index'] for a in plan['sequence']]
            self.assertNotIn(4, indices)
            self.assertNotIn(7, indices)
            self.assertLessEqual(sum(next(int(c['cost']) for c in s['player']['hand'] if c['index'] == i)
                                     for i in indices), 2)

    def test_candidate_count_is_bounded_and_inputs_unchanged(self):
        s = state([dict(card(i), type='Attack') for i in range(8)],
                  [enemy(hp=40), enemy(hp=20, name='b')], energy=3)
        before = copy.deepcopy(s)
        result = search_turn(s, include_candidates=True)
        before_result = copy.deepcopy(result)
        menu = turn_candidates(s, result)
        self.assertLessEqual(len(menu['plans']), 5)
        self.assertEqual(s, before)
        self.assertEqual(result, before_result)

    def test_no_candidates_on_enemy_turn_or_outside_combat(self):
        self.assertIsNone(turn_candidates({'state_type': 'map'}))
        s = state(); s['battle']['turn'] = 'enemy'
        self.assertIsNone(turn_candidates(s))

    def test_model_receives_candidates_and_can_reject_them(self):
        s = state([dict(card(2), type='Attack')])
        s['battle']['enemies'][0]['status'] = [{'id': 'THORNS_POWER'}]
        decision = {'advice': '候選不適用，先喝藥水。', 'action': 'use_potion', 'stop': False,
                    'parameters_json': '{"slot":0}'}
        process = Mock(returncode=0)
        process.communicate.return_value = ('{}', '')
        c = engine.Controller(lambda m: None)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, \
             patch.object(engine, 'ROOT', Path(directory)), \
             patch.object(engine, 'instructions', return_value='instructions'), \
             patch.object(engine, 'enemy_notes', return_value=None), \
             patch.object(engine, 'monster_intel', return_value=None), \
             patch.object(engine.models, 'build_command', return_value=(['codex.exe'], 'codex')), \
             patch.object(engine.models, 'parse_answer', return_value=decision), \
             patch.object(engine.subprocess, 'Popen', return_value=process):
            self.assertEqual(c.decide(s), decision)
        prompt = process.communicate.call_args.args[0]
        payload = json.loads(prompt.split('遊戲狀態：', 1)[1])
        self.assertTrue(payload['turn_candidates']['plans'])
        self.assertIn('拒絕', payload['turn_candidates']['instructions'])


if __name__ == '__main__': unittest.main()
