import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vakuu'))
from combat.search import search_turn
from combat.triggers import after_card_triggers, trigger_facts
from takeover.plans import start_turn_plan, plan_decision
from combat.choices import choice_offer
from test_search import card, enemy, state


def panache(left=5, damage=10):
    return {'id': 'PANACHE_POWER', 'name': '神气制胜', 'amount': left, 'type': 'Buff',
            'description': f'如果你在本回合再打出{left}张牌，就对所有敌人造成{damage}点伤害。', 'keywords': []}


class CombatTriggerTests(unittest.TestCase):
    def test_zero_damage_attack_still_has_a_target(self):
        s = state([card(0, damage=0)], [enemy(hp=10)], energy=1)
        s['player']['status'] = [panache(left=1)]
        result = search_turn(s, include_candidates=True)
        actions = [a for p in result['candidates'] for a in p['sequence']]
        self.assertTrue(actions)
        self.assertTrue(all(a.get('target') == 'a' for a in actions))

    def test_candidate_prompt_replaces_verified_trigger_prose_without_mutating_state(self):
        s = state([dict(card(0), name='attack')], [enemy(hp=99)])
        s['player']['max_hp'] = 30
        s['player']['status'] = [panache()]
        original = copy.deepcopy(s)
        offer = choice_offer(s)
        self.assertIsNotNone(offer)
        self.assertEqual(offer['payload']['card_trigger_counts'][0]['cards_left'], 5)
        self.assertEqual(offer['payload']['player']['status'], [])
        self.assertEqual(s, original)

    def test_queue_tracks_creature_after_entity_suffix_is_reused(self):
        s = state([dict(card(i), name=f'a{i}') for i in range(2)],
                  [dict(enemy(name='CORPSE_SLUG_0'), combat_id=2),
                   dict(enemy(name='CORPSE_SLUG_1'), combat_id=3)])
        s['battle']['round'] = 1
        d = {'action':'play_card', 'parameters_json':'{"card_index":0,"target":"CORPSE_SLUG_0"}',
             'next_cards':[{'hand_index':1, 'target':'CORPSE_SLUG_1'}]}
        plan = start_turn_plan(s, d)
        after = copy.deepcopy(s)
        after['player']['hand'] = [dict(s['player']['hand'][1], index=0)]
        after['battle']['enemies'] = [dict(enemy(name='CORPSE_SLUG_0'), combat_id=3)]
        move = plan_decision(plan, after)
        self.assertEqual(json.loads(move['parameters_json'])['target'], 'CORPSE_SLUG_0')
        d['next_cards'][0]['target'] = 'CORPSE_SLUG_0'
        plan = start_turn_plan(s, d)
        self.assertIsNone(plan_decision(plan, after))  # The original creature died; its label was reused.

    def test_vulnerable_is_rounded_per_hit_not_after_total(self):
        twin = {'id': 'TWIN_STRIKE', 'index': 0, 'cost': '1', 'can_play': True,
                'target_type': 'AnyEnemy', 'description': '造成7点伤害两次。'}
        s = state([twin], [enemy(hp=21)], energy=1)
        s['battle']['enemies'][0]['status'] = [{'id': 'VULNERABLE_POWER', 'amount': 1}]
        result = search_turn(s)
        self.assertEqual(result['predicted_enemy_hp'], [1])
        self.assertNotEqual(result['reason'], 'direct_win')

    def test_bash_applies_vulnerable_after_its_hit(self):
        bash = {'id': 'BASH', 'index': 0, 'cost': '2', 'can_play': True,
                'target_type': 'AnyEnemy', 'description': '造成8点伤害。给予2层易伤。'}
        s = state([bash, card(1)], [enemy(hp=17)], energy=3)
        result = search_turn(s)
        self.assertEqual(result['reason'], 'direct_win')
        self.assertEqual([a['card_index'] for a in result['sequence']], [0, 1])
        s['battle']['enemies'][0]['status'] = [{'id': 'ARTIFACT_POWER', 'amount': 1}]
        self.assertEqual(search_turn(s)['reason'], 'unmodeled_artifact_debuff')
        s['battle']['enemies'][0]['status'] = []
        s['player']['status'] = [{'id': 'WEAK_POWER', 'amount': 1}]
        self.assertEqual(search_turn(s)['reason'], 'combined_damage_rounding')

    def test_slug_death_stuns_survivors_and_records_future_strength(self):
        s = state([card(0)], [enemy(hp=6, name='CORPSE_SLUG_0'),
                             enemy(hp=50, attack=30, name='CORPSE_SLUG_1')], energy=1)
        for e in s['battle']['enemies']:
            e['status'] = [{'id': 'RAVENOUS_POWER', 'amount': 5}]
        result = search_turn(s, include_candidates=True)
        self.assertEqual(result['predicted_hp_loss'], 0)
        plan = next(p for p in result['candidates'] if p['sequence'])
        self.assertEqual(plan['stunned_enemies'], ['CORPSE_SLUG_1'])
        self.assertEqual(plan['future_enemy_strength_gain'], {'CORPSE_SLUG_1': 5})
        self.assertEqual(plan['prevented_attack_damage'], 38)

    def test_panache_needs_five_other_cards_and_is_not_vulnerable_damage(self):
        s = state([card(i, block=0, cost=0) for i in range(4)], [enemy(hp=15)])
        s['player']['status'] = [panache()]
        result = search_turn(s, include_candidates=True)
        self.assertEqual(result['predicted_enemy_hp'], [15])
        self.assertTrue(all(p['panache_triggers'] == 0 for p in result['candidates']))
        s['player']['hand'].append(card(4, block=0, cost=0))
        s['battle']['enemies'][0]['status'] = [{'id': 'VULNERABLE_POWER', 'amount': 1}]
        self.assertEqual(search_turn(s)['predicted_enemy_hp'], [5])
        s['player']['hand'].append({'id': 'PANACHE', 'index': 9,
            'description': '每当你在一回合内打出五张牌时，对所有敌人造成10点伤害。'})
        self.assertFalse(trigger_facts(s['player'])[-1]['activation_counts'])

    def test_unknown_counter_or_death_rule_does_not_claim_forecast(self):
        s = state()
        s['player']['status'] = [panache()]
        s['player']['status'][0]['amount'] = 4
        self.assertEqual(search_turn(s)['reason'], 'unknown_trigger_counter')
        s['player']['status'] = []
        s['battle']['enemies'][0]['status'] = [{'id': 'RAVENOUS_POWER', 'amount': 5}]
        self.assertEqual(search_turn(s)['reason'], 'unknown_death_trigger')

    def test_candidate_queue_accepts_expected_counter_change_but_not_other_changes(self):
        s = state([dict(card(i, block=1, cost=0), name=f'd{i}') for i in range(3)])
        s['battle']['round'] = 1
        s['player']['status'] = [panache()]
        d = {'action':'play_card', 'parameters_json':'{"card_index":0}', 'candidate_id':'P1',
             'next_cards':[{'hand_index':1}, {'hand_index':2}]}
        plan = start_turn_plan(s, d)
        after = copy.deepcopy(s)
        after['player']['hand'] = s['player']['hand'][1:]
        after['player']['status'] = after_card_triggers(s['player']['status'], s['player']['hand'][0])
        after['player']['hand'][0]['index'] = 0
        after['player']['hand'][1]['index'] = 1
        self.assertIsNotNone(plan_decision(plan, after))
        self.assertEqual(plan['guarded_status'][0]['amount'], 3)
        after['player']['status'].append({'id': 'UNEXPECTED_POWER'})
        self.assertIsNone(plan_decision(plan, after))
