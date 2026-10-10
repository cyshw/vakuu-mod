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
    def test_empty_partial_choices_are_not_advertised(self):
        menu = {'search_reason': 'unsupported_hand', 'plans': [
            {'id': 'P1', 'sequence': [], 'confidence': 'partial', 'end_turn': True},
            {'id': 'P2', 'sequence': [{'card_index': 1}], 'confidence': 'partial', 'end_turn': False}]}
        with patch('vakuu_choices.turn_candidates', return_value=menu):
            offer = choice_offer(sample())
        self.assertEqual(offer['schema']['properties']['choice']['enum'], ['P2', 'reject'])
        self.assertEqual(len(offer['payload']['plans']), 1)
        menu['plans'] = menu['plans'][:1]
        with patch('vakuu_choices.turn_candidates', return_value=menu):
            self.assertIsNone(choice_offer(sample()))

    def test_held_void_is_known_but_changed_description_still_blocks(self):
        s = sample()
        void = {'id': 'VOID', 'index': 3, 'type': 'Status', 'cost': '0', 'can_play': False,
                'description': '不能被打出。 虚无。 每当你抽到这张牌时，失去[defect_energy_icon.png]。'}
        s['player']['hand'].append(void)
        self.assertIsNotNone(choice_offer(s))
        void['description'] += '每当你打出一张牌时失去1点生命。'
        self.assertIsNone(choice_offer(s))

    def test_mixed_orbs_only_offer_non_orb_supported_prefixes(self):
        s = sample()
        s['player']['hand'].append({'id': 'ZAP', 'index': 3, 'name': '電擊', 'cost': '1', 'can_play': True,
                                   'target_type': 'Self', 'type': 'Skill', 'description': '生成1个闪电充能球。'})
        s['player'].update(orb_slots=3, orbs=[{'id': 'LIGHTNING_ORB', 'passive_val': 3, 'evoke_val': 8},
                                            {'id': 'FROST_ORB', 'passive_val': 2, 'evoke_val': 5}])
        offer = choice_offer(s)
        self.assertIsNotNone(offer)
        self.assertEqual(offer['menu']['search_reason'], 'mixed_orb_order')
        self.assertTrue(any(len(p['sequence']) >= 2 for p in offer['menu']['plans']))
        for plan in offer['menu']['plans']:
            self.assertFalse(plan['end_turn'])
            self.assertEqual(plan['confidence'], 'unverified')
            self.assertNotIn('predicted_hp_loss', plan)
            self.assertTrue(all(a['card_index'] in {0, 1} for a in plan['sequence']))

    def test_mixed_orb_attack_prefix_does_not_require_wasting_energy_on_block(self):
        s = sample()
        s['player']['energy'] = 3
        s['player']['hand'].append(dict(s['player']['hand'][0], index=3))
        s['player']['hand'].append({'id': 'ZAP', 'index': 4, 'cost': '1', 'can_play': True,
                                   'target_type': 'Self', 'type': 'Skill', 'description': '生成1个闪电充能球。'})
        s['player']['orbs'] = [{'id': 'LIGHTNING_ORB', 'passive_val': 3, 'evoke_val': 8},
                               {'id': 'FROST_ORB', 'passive_val': 2, 'evoke_val': 5}]
        s['player']['orb_slots'] = 3
        s['battle']['enemies'][0]['intents'][0]['label'] = '0'
        offer = choice_offer(s)
        self.assertTrue(any([a['card_index'] for a in p['sequence']] == [0, 3]
                            and not p['end_turn'] for p in offer['menu']['plans']))

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


    def test_claude_gets_the_full_state_without_a_candidate_round(self):
        c = engine.Controller(lambda m: None)
        full = {'advice': '', 'stop': False, 'action': 'end_turn', 'parameters_json': '{}', 'next_cards': []}
        with patch.object(engine, 'choice_offer') as offer, patch.object(engine.api, 'compact', side_effect=lambda x: copy.deepcopy(x)),              patch.object(c, 'call_model', return_value=full) as call:
            c.decide(sample(), {'model_key': 'claude:sonnet', 'effort': 'low'})
        offer.assert_not_called()
        self.assertEqual(call.call_count, 1)

    def test_a_non_winning_search_does_not_replace_the_models_turn_plan(self):
        s = sample()
        plan = {'advice': '', 'stop': False, 'action': 'play_card', 'parameters_json': json.dumps({'card_index': 0, 'target': 'a'}),
                'next_cards': [{'hand_index': 1, 'target': '', 'potion_slot': -1}]}
        second = copy.deepcopy(s); second['player']['hand'] = second['player']['hand'][1:]
        for i, card in enumerate(second['player']['hand']): card['index'] = i
        second['player']['energy'] = 1
        searched = {'decision': {'action': 'end_turn', 'stop': False, 'parameters_json': '{}', 'advice': ''},
                    'reason': 'supported_turn'}
        c = engine.Controller(lambda m: None)
        with patch.object(engine.api, 'request', side_effect=[s, s, {'status': 'ok'}, second, second, {'status': 'ok'}]) as api,              patch.object(engine, 'search_turn', return_value=searched),              patch.object(c, 'decide_routed', return_value=plan) as model, patch.object(c, 'wait_for_change'):
            c.run(True, 2)
        self.assertEqual(model.call_count, 1)
        posts = [call.args[1] for call in api.call_args_list if len(call.args) == 2]
        self.assertEqual(posts, [{'action': 'play_card', 'card_index': 0, 'target': 'a'}, {'action': 'play_card', 'card_index': 0}])


if __name__ == '__main__': unittest.main()
