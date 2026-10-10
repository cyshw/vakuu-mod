import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'vakuu'))
import vakuu_engine as engine
import vakuu_plans as plans
from vakuu_choices import choice_offer, chosen_decision
from test_search import card, enemy, state


def sample():
    s = state([dict(card(0), name='打擊', type='Attack'), dict(card(1, block=5), name='防禦', type='Skill'),
               {'id': 'COOLHEADED', 'index': 2, 'name': '冷靜頭腦', 'cost': '1', 'can_play': True,
                'target_type': 'Self', 'type': 'Skill', 'description': '生成1个冰霜充能球。 抽1张牌。'}],
              [enemy(hp=40, attack=4)], energy=2, hp=30)
    s['player']['max_hp'] = 40
    s['player']['deck'] = [{'id': 'SOME_DECK_CARD', 'name': '不應出现在精簡輸入'}]
    s['battle']['round'] = 1
    return s


class ChoiceTests(unittest.TestCase):
    def test_random_lightning_offers_unverified_prefixes_without_forecasts(self):
        s = sample()
        s['battle']['enemies'].append(enemy(hp=40, attack=4, name='b'))
        s['player'].update(orb_slots=3, orbs=[{'id': 'LIGHTNING_ORB', 'passive_val': 3, 'evoke_val': 8}])
        offer = choice_offer(s)
        self.assertIsNotNone(offer)
        self.assertEqual(offer['menu']['search_reason'], 'random_orb_targets')
        for plan in offer['menu']['plans']:
            self.assertEqual(plan['confidence'], 'unverified')
            self.assertFalse(plan['end_turn'])
            self.assertNotIn('predicted_hp_loss', plan)
        decision = chosen_decision(s, offer, {'choice': offer['menu']['plans'][0]['id'], 'reject_reason': '', 'reason': '起手'})
        self.assertNotIn(-1, [c['hand_index'] for c in decision['next_cards']])

    def test_offer_has_relevant_cards_but_no_full_hand_or_deck(self):
        s = sample(); before = copy.deepcopy(s)
        offer = choice_offer(s)
        self.assertIsNotNone(offer)
        self.assertNotIn('hand', offer['payload']['player'])
        self.assertNotIn('deck', offer['payload']['player'])
        self.assertNotIn('不應出现在精簡輸入', offer['prompt'])
        self.assertIn('battle', offer['payload'])
        self.assertIn('description', offer['payload']['candidate_cards'][0])
        self.assertEqual(s, before)

    def test_danger_unknown_trigger_and_potions_bypass_small_prompt(self):
        for variant in ('lethal', 'low_hp', 'unknown_enemy', 'potion'):
            s = sample()
            if variant == 'lethal': s['battle']['enemies'][0]['intents'][0]['label'] = '40'
            elif variant == 'low_hp': s['player']['hp'] = 8
            elif variant == 'unknown_enemy': s['battle']['enemies'][0]['status'] = [{'id': 'THORNS_POWER'}]
            else: s['player']['potions'] = [{'can_use_in_combat': True}]
            with self.subTest(variant): self.assertIsNone(choice_offer(s))

    def test_choice_id_translates_stored_indices_and_target(self):
        s = sample()
        menu = {'plans': [{'id': 'P2', 'sequence': [{'card_index': 0, 'target': 'a'}, {'card_index': 1}],
                           'confidence': 'simulated', 'end_turn': True}]}
        answer = {'choice': 'P2', 'reject_reason': '', 'reason': '先攻後防。'}
        decision = chosen_decision(s, {'menu': menu}, answer)
        self.assertEqual(json.loads(decision['parameters_json']), {'card_index': 0, 'target': 'a'})
        self.assertEqual([c['hand_index'] for c in decision['next_cards']], [1, -1])
        self.assertEqual(decision['candidate_id'], 'P2')

    def test_partial_and_draw_choices_never_auto_end_turn(self):
        s = sample()
        menu = {'plans': [{'id': 'P1', 'sequence': [{'card_index': 2}], 'confidence': 'partial',
                           'end_turn': True, 'requires_replan': True}]}
        d = chosen_decision(s, {'menu': menu}, {'choice': 'P1', 'reject_reason': '', 'reason': '抽牌再算'})
        self.assertEqual(d['next_cards'], [])

    def test_bad_id_duplicate_or_missing_cards_cannot_be_executed(self):
        s = sample()
        for sequence in ([{'card_index': 99}], [{'card_index': 0}, {'card_index': 0}]):
            offer = {'menu': {'plans': [{'id': 'P1', 'sequence': sequence}]}}
            with self.assertRaises(ValueError):
                chosen_decision(s, offer, {'choice': 'P1', 'reject_reason': '', 'reason': ''})
        with self.assertRaises(ValueError):
            chosen_decision(s, {'menu': {'plans': []}}, {'choice': 'P9', 'reject_reason': '', 'reason': ''})

    def test_acceptance_calls_model_once_with_short_schema(self):
        s = sample(); offer = choice_offer(s)
        answer = {'choice': offer['menu']['plans'][0]['id'], 'reject_reason': '', 'reason': '採用方案'}
        c = engine.Controller(lambda m: None)
        with patch.object(c, 'call_model', return_value=answer) as call:
            decision = c.decide(s, {'model_key': 'codex:gpt-6.1-sol', 'effort': 'low'})
        self.assertEqual(call.call_count, 1)
        self.assertTrue(call.call_args.kwargs['candidate'])
        self.assertEqual(decision['candidate_id'], answer['choice'])
        self.assertNotIn('next_cards', call.call_args.args[1]['properties'])

    def test_rejection_adds_full_hand_once_and_preserves_alternative_action(self):
        s = sample()
        alternative = {'action': 'end_turn', 'parameters_json': '{}', 'advice': '另一方案', 'stop': False}
        c = engine.Controller(lambda m: None)
        reject = {'choice': 'reject', 'reject_reason': 'insufficient_information', 'reason': '需要看其他手牌'}
        with patch.object(c, 'call_model', side_effect=[reject, alternative]) as call, \
             patch.object(engine, 'enemy_notes', return_value=None), patch.object(engine, 'monster_intel', return_value=None):
            self.assertEqual(c.decide(s), alternative)
        self.assertEqual(call.call_count, 2)
        full = json.loads(call.call_args_list[1].args[0].split('遊戲狀態：', 1)[1])
        self.assertEqual(len(full['player']['hand']), 3)
        self.assertEqual(full['candidate_rejection']['reason'], 'insufficient_information')

    def test_malformed_reply_falls_back_but_quota_errors_do_not_retry(self):
        s = sample(); c = engine.Controller(lambda m: None)
        alternative = {'action': 'end_turn', 'parameters_json': '{}', 'advice': '另一方案', 'stop': False}
        with patch.object(c, 'call_model', side_effect=[{'choice': 'bad'}, alternative]) as call, \
             patch.object(engine, 'enemy_notes', return_value=None), patch.object(engine, 'monster_intel', return_value=None):
            self.assertEqual(c.decide(s), alternative)
        self.assertEqual(call.call_count, 2)
        with patch.object(c, 'call_model', side_effect=RuntimeError('quota')) as call:
            with self.assertRaises(RuntimeError): c.decide(s)
        self.assertEqual(call.call_count, 1)

    def test_guarded_queue_survives_index_shift_but_stops_on_changed_cost_or_draw(self):
        s = sample()
        offer = {'menu': {'plans': [{'id': 'P1', 'sequence': [{'card_index': 0, 'target': 'a'}, {'card_index': 1}],
                                   'confidence': 'simulated', 'end_turn': True}]}}
        d = chosen_decision(s, offer, {'choice': 'P1', 'reject_reason': '', 'reason': ''})
        next_state = copy.deepcopy(s)
        next_state['player']['hand'] = next_state['player']['hand'][1:]
        for i, c in enumerate(next_state['player']['hand']): c['index'] = i
        next_state['player']['energy'] = 1
        plan = plans.start_plan(s, d)
        move = plans.plan_decision(plan, next_state)
        self.assertEqual(json.loads(move['parameters_json']), {'card_index': 0})
        for change in ('cost', 'draw', 'status', 'energy'):
            altered = copy.deepcopy(next_state)
            if change == 'cost': altered['player']['hand'][0]['cost'] = '0'
            elif change == 'draw': altered['player']['hand'].append(dict(card(2), name='新牌'))
            elif change == 'status': altered['player']['status'] = [{'id': 'TENDER_POWER'}]
            else: altered['player']['energy'] = 2
            with self.subTest(change): self.assertIsNone(plans.plan_decision(plans.start_plan(s, d), altered))

    def test_controller_executes_stored_queue_without_reasking_or_reordering(self):
        s = sample()
        offer = {'menu': {'plans': [{'id': 'P1', 'sequence': [{'card_index': 0, 'target': 'a'}, {'card_index': 1}],
                                   'confidence': 'simulated', 'end_turn': True}]}}
        d = chosen_decision(s, offer, {'choice': 'P1', 'reject_reason': '', 'reason': ''})
        second = copy.deepcopy(s); second['player']['hand'] = second['player']['hand'][1:]
        for i, c in enumerate(second['player']['hand']): c['index'] = i
        second['player']['energy'] = 1
        c = engine.Controller(lambda m: None)
        responses = [s, s, {'status': 'ok'}, second, second, {'status': 'ok'}]
        with patch.object(engine.api, 'request', side_effect=responses) as api, \
             patch.object(engine, 'search_turn', return_value={'decision': None, 'reason': 'unsupported_hand'}) as search, \
             patch.object(c, 'decide_routed', return_value=d) as model, patch.object(c, 'wait_for_change'):
            c.run(True, 2)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(search.call_count, 1)  # The selected order takes precedence on the next step.
        posts = [call.args[1] for call in api.call_args_list if len(call.args) == 2]
        self.assertEqual(posts, [{'action': 'play_card', 'card_index': 0, 'target': 'a'},
                                 {'action': 'play_card', 'card_index': 0}])


if __name__ == '__main__': unittest.main()
