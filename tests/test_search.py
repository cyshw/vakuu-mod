import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'vakuu'))
import vakuu_engine as engine
from vakuu_search import search_turn


def card(index, damage=6, block=None, cost=1):
    return {'id': 'DEFEND_DEFECT' if block is not None else 'STRIKE_DEFECT',
            'index': index, 'cost': str(cost), 'can_play': True,
            'target_type': 'Self' if block is not None else 'AnyEnemy',
            'description': f'获得{block}点格挡。' if block is not None else f'造成{damage}点伤害。'}


def enemy(hp=12, attack=8, name='a', block=0):
    return {'entity_id': name, 'hp': hp, 'block': block, 'status': [],
            'intents': [{'type': 'Attack', 'label': str(attack)}]}


def state(hand=None, enemies=None, energy=2, hp=20):
    return {'state_type': 'monster', 'battle': {'turn': 'player', 'is_play_phase': True,
            'enemies': enemies or [enemy()]},
            'player': {'hand': hand if hand is not None else [card(0), card(1, block=5)],
                       'energy': energy, 'hp': hp, 'block': 0, 'status': [], 'relics': [], 'potions': []}}


class SearchTests(unittest.TestCase):
    def test_pickup_only_holster_and_known_held_cards_allow_basic_candidates(self):
        for cid, description in (
            ('COLD_SNAP', '造成6点伤害。 生成1个冰霜充能球。'),
            ('UPROAR', '造成6点伤害两次。 随机打出你的抽牌堆中的1张攻击牌。'),
            ('COMPILE_DRIVER', '造成7点伤害。 你每有一种不同的充能球，就抽一张牌。 （抽1张牌）'),
        ):
            s = state(enemies=[enemy(hp=99)])
            s['player']['relics'] = [{'id': 'PHIAL_HOLSTER'}, {'id': 'THE_COURIER'}]
            held = {'id': cid, 'index': 2, 'cost': '1', 'description': description, 'can_play': True}
            s['player']['hand'].append(held)
            with self.subTest(cid):
                self.assertEqual(search_turn(s)['reason'], 'unsupported_hand')
                held['description'] += '每当你打出一张牌时，失去1点生命。'
                self.assertEqual(search_turn(s)['reason'], 'unknown_hand_trigger')

    def test_kill_prevents_attack_instead_of_wasting_energy_on_block(self):
        s = state([card(8), card(3, block=20)], [enemy(hp=6)], energy=1)
        result = search_turn(s)
        self.assertEqual(result['reason'], 'direct_win')
        self.assertEqual(json.loads(result['decision']['parameters_json']), {'card_index': 8, 'target': 'a'})

    def test_target_with_largest_attack_is_killed_first(self):
        s = state([card(4)], [enemy(hp=6, attack=1), enemy(hp=6, attack=15, name='b')], energy=1)
        result = search_turn(s)
        self.assertEqual(json.loads(result['decision']['parameters_json'])['target'], 'b')
        self.assertEqual(result['predicted_hp_loss'], 1)

    def test_survival_beats_damage(self):
        result = search_turn(state([card(0, damage=10), card(1, block=8)], [enemy(hp=20)], energy=1, hp=5))
        self.assertEqual(json.loads(result['decision']['parameters_json']), {'card_index': 1})
        self.assertEqual(result['predicted_hp_loss'], 0)

    def test_enemy_block_and_multihit_intent(self):
        s = state([card(0, damage=10), card(1, block=5)], [enemy(hp=6, attack='4×3', block=5)], energy=1)
        result = search_turn(s)
        self.assertEqual(result['predicted_hp_loss'], 7)
        self.assertEqual(json.loads(result['decision']['parameters_json']), {'card_index': 1})

    def test_hand_effects_cannot_be_silently_ignored(self):
        s = state([card(0), {'id': 'BURN', 'can_play': False,
                           'description': '不能被打出。 在你的回合结束时，如果这张牌在你的手牌中，你受到2点伤害。'}])
        self.assertIsNone(search_turn(s)['decision'])
        # A direct kill avoids turn-end effects, so supported cards may still finish it.
        s['battle']['enemies'][0]['hp'] = 6
        self.assertEqual(search_turn(s)['reason'], 'direct_win')

    def test_known_unsupported_playable_card_allows_proven_direct_win(self):
        s = state([card(0), {'id': 'COOLHEADED', 'can_play': True,
                           'description': '生成1个冰霜充能球。 抽1张牌。'}], [enemy(hp=6)])
        self.assertEqual(search_turn(s)['reason'], 'direct_win')
        s['battle']['enemies'][0]['hp'] = 20
        self.assertIsNone(search_turn(s)['decision'])

    def test_unknown_held_trigger_blocks_even_a_lethal_combo(self):
        s = state([card(0), {'id': 'PAIN', 'can_play': False,
                           'description': '每打出一张牌失去1点生命。'}], [enemy(hp=6)], hp=1)
        self.assertEqual(search_turn(s)['reason'], 'unknown_hand_trigger')

    def test_thorns_death_trigger_and_relics_require_model(self):
        for kind, effect in [('enemy', 'THORNS_POWER'), ('enemy', 'HARD_TO_KILL_POWER'),
                             ('player', 'SHRINK_POWER'), ('relic', 'KUNAI')]:
            s = state([card(0)], [enemy(hp=6)])
            if kind == 'enemy': s['battle']['enemies'][0]['status'] = [{'id': effect}]
            elif kind == 'player': s['player']['status'] = [{'id': effect}]
            else: s['player']['relics'] = [{'id': effect}]
            with self.subTest(effect): self.assertIsNone(search_turn(s)['decision'])

    def test_modified_card_description_requires_model(self):
        s = state()
        s['player']['hand'][0]['description'] += '失去3点生命。'
        self.assertIsNone(search_turn(s)['decision'])

    def test_potion_is_not_ignored_when_losing_hp(self):
        s = state([card(0)], [enemy(hp=30)], hp=5)
        s['player']['potions'] = [{'can_use_in_combat': True}]
        self.assertIsNone(search_turn(s)['decision'])
        s['player']['hp'] = 20
        self.assertEqual(search_turn(s)['reason'], 'needs_potion_evaluation')

    def test_search_limit_never_uses_partial_best_result(self):
        self.assertEqual(search_turn(state(), max_nodes=1)['reason'], 'search_limit')
        self.assertIsNone(search_turn(state(), max_seconds=0)['decision'])

    def test_frost_block_and_single_target_lightning_are_counted(self):
        s = state([card(0)], [enemy(hp=20)], energy=1)
        s['player'].update(orb_slots=3, orbs=[{'id': 'FROST_ORB', 'passive_val': 5, 'evoke_val': 5},
                                           {'id': 'LIGHTNING_ORB', 'passive_val': 3, 'evoke_val': 8}])
        result = search_turn(s)
        self.assertEqual(result['predicted_hp_loss'], 3)
        self.assertEqual(result['predicted_enemy_hp'], [11])

    def test_random_orb_target_requires_model(self):
        s = state(enemies=[enemy(), enemy(name='b')])
        s['player'].update(orb_slots=3, orbs=[{'id': 'LIGHTNING_ORB', 'passive_val': 3, 'evoke_val': 8}])
        self.assertEqual(search_turn(s)['reason'], 'random_orb_targets')

    def test_dualcast_can_find_a_direct_kill(self):
        c = {'id': 'DUALCAST', 'index': 7, 'cost': '1', 'target_type': 'Self', 'can_play': True,
             'description': '激发你最右侧的充能球两次。'}
        s = state([c], [enemy(hp=16)])
        s['player'].update(orb_slots=3, orbs=[{'id': 'LIGHTNING_ORB', 'passive_val': 3, 'evoke_val': 8}])
        result = search_turn(s)
        self.assertEqual(result['reason'], 'direct_win')
        self.assertEqual(result['predicted_enemy_hp'], [0])

    def test_channel_into_full_identical_queue_evokes_then_passives(self):
        zap = {'id': 'ZAP', 'index': 4, 'cost': '1', 'target_type': 'Self', 'can_play': True,
               'description': '生成1个闪电充能球。'}
        s = state([zap], [enemy(hp=40)], energy=1)
        s['player'].update(orb_slots=1, orbs=[{'id': 'LIGHTNING_ORB', 'passive_val': 3, 'evoke_val': 8}])
        result = search_turn(s)
        self.assertEqual(result['predicted_enemy_hp'], [29])
        self.assertEqual(json.loads(result['decision']['parameters_json']), {'card_index': 4})

    def test_current_modifiers_are_not_applied_twice(self):
        s = state([card(0, damage=4), card(1, block=3)], [enemy(hp=30)], energy=2)
        s['player']['status'] = [{'id': 'WEAK_POWER', 'amount': 1}, {'id': 'FRAIL_POWER', 'amount': 1}]
        result = search_turn(s)
        self.assertEqual(result['predicted_enemy_hp'], [26])
        self.assertEqual(result['predicted_hp_loss'], 5)

    def test_mixed_queue_is_declined_when_evoking(self):
        s = state([{'id': 'DUALCAST', 'index': 4, 'cost': '1', 'target_type': 'Self', 'can_play': True,
                    'description': '激发你最右侧的充能球两次。'}])
        s['player'].update(orb_slots=3, orbs=[{'id': 'LIGHTNING_ORB', 'passive_val': 3, 'evoke_val': 8},
                                           {'id': 'FROST_ORB', 'passive_val': 2, 'evoke_val': 5}])
        self.assertEqual(search_turn(s)['reason'], 'mixed_orb_order')

    def test_search_does_not_mutate_live_state(self):
        s = state(); before = copy.deepcopy(s)
        search_turn(s)
        self.assertEqual(s, before)

    def test_unknown_intents_and_missing_numbers_require_model(self):
        for label in ('', '???'):
            s = state(); s['battle']['enemies'][0]['intents'][0]['label'] = label
            self.assertIsNone(search_turn(s)['decision'])
        s = state(); s['battle']['enemies'][0]['intents'][0]['type'] = 'DeathBlow'
        self.assertIsNone(search_turn(s)['decision'])

    def test_damage_adding_statuses_allow_only_kills(self):
        echo = {'id': 'ECHO_FORM_POWER', 'amount': 1}
        kill = state([card(0, damage=40)], [enemy(hp=24)], energy=1); kill['player']['status'] = [echo]
        self.assertEqual(search_turn(kill)['reason'], 'direct_win')
        plain = state([card(0)], [enemy(hp=30)], energy=1); plain['player']['status'] = [echo]
        self.assertIsNone(search_turn(plain)['decision'])
        shrunk = state([card(0, damage=40)], [enemy(hp=24)], energy=1); shrunk['player']['status'] = [{'id': 'SHRINK_POWER'}]
        self.assertEqual(search_turn(shrunk)['reason'], 'unknown_player_status')  # Cuts damage: never ignored.

    def test_controller_plays_found_kills_and_asks_the_model_otherwise(self):
        kill = state([card(9, damage=40)], [enemy(hp=24)], energy=1)
        c = engine.Controller(lambda m: None)
        with patch.object(engine.api, 'request', side_effect=[kill, kill, {'status': 'ok'}]) as api,              patch.object(c, 'decide_routed') as model, patch.object(c, 'wait_for_change'):
            c.run(True, 1)
        model.assert_not_called()
        self.assertEqual([call.args[1]['card_index'] for call in api.call_args_list if len(call.args) == 2], [9])
        # A searched turn that does not win values this turn only (no setup, no held cards): the model decides.
        plain = state([card(0)], [enemy(hp=30)], energy=1)
        answer = {'advice': '打', 'stop': False, 'action': 'play_card', 'parameters_json': '{"card_index":0,"target":"a"}',
                  'next_cards': []}
        c = engine.Controller(lambda m: None)
        with patch.object(engine.api, 'request', side_effect=[plain, plain, {'status': 'ok'}]),              patch.object(c, 'decide_routed', return_value=answer) as model, patch.object(c, 'wait_for_change'):
            c.run(True, 1)
        model.assert_called_once()

    def test_no_ai_mode_stops_without_model_or_game_action(self):
        c = engine.Controller(lambda m: None, allow_model=False)
        with patch.object(engine.api, 'request', return_value={'state_type': 'event',
                          'event': {'options': [{'index': 0}, {'index': 1}]}}) as api, \
             patch.object(c, 'decide_routed') as model:
            c.run(True, 1)
        model.assert_not_called()
        self.assertTrue(all(len(call.args) == 1 for call in api.call_args_list))

    def test_program_comparison_selects_survival_without_model(self):
        s = state([card(0, damage=10), card(1, block=8)], [enemy(hp=20)], energy=1, hp=5)
        c = engine.Controller(lambda m: None, program_comparison=True)
        self.assertFalse(c.allow_model)
        with patch.object(engine.api, 'request', side_effect=[s, s, {'status':'ok'}]) as api, \
             patch.object(c, 'decide_routed') as model, patch.object(c, 'wait_for_change'):
            c.run(True,1)
        model.assert_not_called()
        self.assertEqual([call.args[1] for call in api.call_args_list if len(call.args)==2],
                         [{'action':'play_card','card_index':1}])

    def test_program_comparison_stops_on_unmodeled_relic(self):
        s = state()
        s['player']['relics'] = [{'id':'MUMMIFIED_HAND'}]
        c = engine.Controller(lambda m: None, program_comparison=True)
        with patch.object(engine.api,'request',return_value=s) as api, patch.object(c,'decide_routed') as model:
            c.run(True,1)
        model.assert_not_called()
        self.assertTrue(all(len(call.args)==1 for call in api.call_args_list))

    def test_program_first_uses_model_only_for_unmodeled_state(self):
        for unknown in (False, True):
            s = state([card(0), card(1,block=8)], [enemy(hp=30)], energy=1)
            if unknown: s['player']['relics'] = [{'id':'MUMMIFIED_HAND'}]
            reports = []
            c = engine.Controller(reports.append, program_first=True)
            answer = {'advice':'defend','stop':False,'action':'play_card',
                      'parameters_json':'{"card_index":1}','next_cards':[]}
            with patch.object(engine.api,'request',side_effect=[s,s,{'status':'ok'}]), \
                 patch.object(c,'decide_routed',return_value=answer) as model, patch.object(c,'wait_for_change'):
                c.run(True,1)
            self.assertEqual(model.call_count, int(unknown))
            if unknown: self.assertTrue(any('unknown_relic' in m for m in reports))
