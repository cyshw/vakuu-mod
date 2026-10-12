import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from test_search import card, enemy, state
from combat.search import evaluate_sequence, search_turn
from combat.text import described_damage, described_block
from core.screens import fight_key, in_fight, same_screen
from takeover import controller as engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'exam'))
import replay_eval
import regression


def decision(sequence, ends=True):
    first = sequence[0] if sequence else {}
    return {'action': 'play_card' if sequence else 'end_turn', 'stop': False,
            'parameters_json': json.dumps(first), 'advice': '',
            'next_cards': [{'hand_index': p['card_index'], 'target': p.get('target')} for p in sequence[1:]]
                          + ([{'hand_index': -1}] if ends and sequence else [])}


class SharedSimulationTests(unittest.TestCase):
    def test_forced_order_does_not_substitute_the_best_plan(self):
        bash = dict(card(0, damage=8, cost=2), id='BASH', description='造成8点伤害。给予2层易伤。')
        twin = dict(card(1, damage=7), id='TWIN_STRIKE', description='造成7点伤害两次。')
        s = state([bash, twin], [enemy(hp=28, attack=8)], energy=3)
        good = [{'card_index': 0, 'target': 'a'}, {'card_index': 1, 'target': 'a'}]
        bad = list(reversed(good))
        self.assertEqual(search_turn(s)['predicted_enemy_hp'], [0])
        self.assertEqual(evaluate_sequence(s, good)['predicted_enemy_hp'], [0])
        self.assertEqual(evaluate_sequence(s, bad)['predicted_enemy_hp'], [6])
        self.assertEqual(replay_eval.score_combat(s, decision(bad))['unblocked'], 8)

    def test_orb_passive_only_counts_if_plan_ends_turn(self):
        s = state([card(0, damage=6)], [enemy(hp=9)], energy=1)
        s['player'].update(orbs=[{'id': 'LIGHTNING_ORB', 'passive_val': 3, 'evoke_val': 8}], orb_slots=1)
        sequence = [{'card_index': 0, 'target': 'a'}]
        self.assertTrue(replay_eval.score_combat(s, decision(sequence))['kills_all'])
        prefix = replay_eval.score_combat(s, decision(sequence, ends=False))
        self.assertFalse(prefix['kills_all'])
        self.assertIsNone(prefix['unblocked'])

    def test_duplicate_card_or_wrong_target_cannot_certify_a_kill(self):
        s = state([card(0)], [enemy(hp=12)], energy=2)
        for sequence in ([{'card_index': 0, 'target': 'a'}] * 2,
                         [{'card_index': 0, 'target': 'missing'}]):
            result = replay_eval.score_combat(s, decision(sequence))
            self.assertIsNone(result['kills_all'])
            self.assertEqual(result['simulation_reason'], 'invalid_or_unresolved_sequence')

    def test_unknown_effect_and_potion_are_unscored_not_safe(self):
        s = state([dict(card(0), description='造成6点伤害。抽1张牌。')])
        d = decision([{'card_index': 0, 'target': 'a'}])
        result = replay_eval.score_combat(s, d)
        self.assertIsNone(result['dies'])
        self.assertIsNone(result['kills_all'])
        self.assertFalse(regression.CHECKS['!='](None, True))
        failures = regression.failures({'type': 'combat', 'state': s,
                                        'expect': {'dies': ['!=', True]}}, d)
        self.assertIn('無法判定', failures[0])
        d['next_cards'].insert(0, {'potion_slot': 0, 'target': 'a'})
        self.assertEqual(replay_eval.evaluate_plan(s, d)['reason'], 'unsupported_plan_potion')

    def test_relic_block_uses_candidate_remaining_hand(self):
        from combat.relics import CONDITIONAL_RELICS
        s = state([card(0, block=1), card(1, block=1)], [enemy(hp=30, attack=2)], energy=1)
        s['player']['relics'] = [{'id': 'CLOAK_CLASP', 'description': CONDITIONAL_RELICS['CLOAK_CLASP'], 'counter': None}]
        result = replay_eval.score_combat(s, decision([{'card_index': 0}]))
        self.assertEqual(result['block'], 1)
        self.assertEqual(result['unblocked'], 0)

    def test_display_parsing_handles_hits_and_traditional_block_without_authorizing_unknown_cards(self):
        self.assertEqual(described_damage('造成7点伤害两次。'), 14)
        self.assertEqual(described_damage('造成7點傷害3次。'), 21)
        self.assertEqual(described_block('獲得 5 點格擋。'), 5)
        s = state([dict(card(0), id='UNKNOWN', description='造成999点伤害。')])
        self.assertIsNone(replay_eval.score_combat(s, decision([{'card_index': 0, 'target': 'a'}]))['kills_all'])
        self.assertEqual(evaluate_sequence(s, [{'card_index': True}])['reason'], 'invalid_sequence')

    def test_all_combat_overlays_share_one_identity(self):
        s = state(); s['run'] = {'act': 1, 'floor': 5}; s['battle']['round'] = 2
        for kind in ('monster', 'hand_select', 'card_select', 'bundle_select', 'relic_select'):
            overlay = {**s, 'state_type': kind}
            self.assertTrue(in_fight(overlay))
            self.assertEqual(fight_key(overlay), (1, 5))
        self.assertIsNone(fight_key({'state_type': 'card_select', 'player': {'deck': []}}))
        self.assertFalse(same_screen(s, {**s, 'run': {'act': 1, 'floor': 6}}))

    def test_execution_telemetry_loss_during_combat_stops_but_reward_finishes(self):
        before = state()
        busy = copy.deepcopy(before); busy['execution'] = {'running': True, 'queue_empty': False}
        for final, raises in ((copy.deepcopy(before), True), ({'state_type': 'rewards'}, False)):
            c = engine.Controller(lambda m: None)
            with patch.object(engine.api, 'request', side_effect=[busy, final]), patch.object(c.stop_event, 'wait', return_value=False):
                if raises:
                    with self.assertRaisesRegex(RuntimeError, '狀態消失'):
                        c.wait_for_change(before, body={'action': 'play_card', 'card_index': 0})
                else:
                    c.wait_for_change(before, body={'action': 'play_card', 'card_index': 0})

    def test_empty_queue_without_action_evidence_times_out(self):
        before = state(); empty = {**before, 'execution': {'running': False, 'queue_empty': True}}
        c = engine.Controller(lambda m: None)
        clock = [0]
        def wait(seconds): clock[0] += seconds; return False
        with patch.object(engine.api, 'request', return_value=empty), patch.object(c.stop_event, 'wait', side_effect=wait), patch.object(engine.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(RuntimeError, '尚未確認'):
                c.wait_for_change(before, timeout=.5, body={'action': 'play_card', 'card_index': 0})
