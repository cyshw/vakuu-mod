import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vakuu'))
from vakuu_search import search_turn
from vakuu_plans import start_turn_plan, plan_decision

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/controlled-combat-triggers.json').read_text(encoding='utf-8'))


class RecordedTriggerTests(unittest.TestCase):
    def assert_prediction_matches_creatures(self, before, after):
        prediction = search_turn(before, include_candidates=True)
        self.assertEqual(prediction['reason'], 'supported_turn')
        expected = {e['combat_id']: hp for e, hp in zip(before['battle']['enemies'], prediction['predicted_enemy_hp'])}
        actual = {e['combat_id']: e['hp'] for e in after['battle']['enemies']}
        self.assertEqual(expected, {cid: actual.get(cid, 0) for cid in expected})

    def test_recorded_fifth_card_and_vulnerable_kill_match_game(self):
        self.assert_prediction_matches_creatures(FIXTURE['panache_before_fifth'], FIXTURE['panache_after_fifth'])
        self.assert_prediction_matches_creatures(FIXTURE['bash_before_twin'], FIXTURE['bash_after_twin'])

    def test_recorded_suffix_reassignment_preserves_both_queue_modes(self):
        s = FIXTURE['queue_initial']
        hand = {c['id']: c for c in s['player']['hand']}
        target, survivor = s['battle']['enemies']
        decision = {'action': 'play_card', 'parameters_json': json.dumps(
            {'card_index': hand['BASH']['index'], 'target': target['entity_id']}),
            'next_cards': [{'hand_index': hand['TWIN_STRIKE']['index'], 'target': target['entity_id']},
                           {'hand_index': hand['SLICE']['index'], 'target': survivor['entity_id']}]}
        for guarded in (False, True):
            d = copy.deepcopy(decision)
            if guarded: d['candidate_id'] = 'P1'
            plan = start_turn_plan(s, d)
            self.assertIsNotNone(plan_decision(plan, FIXTURE['queue_after_bash']))
            move = plan_decision(plan, FIXTURE['queue_after_twin'])
            self.assertIsNotNone(move)
            current = FIXTURE['queue_after_twin']['battle']['enemies'][0]
            self.assertEqual(current['combat_id'], survivor['combat_id'])
            self.assertNotEqual(current['entity_id'], survivor['entity_id'])
            self.assertEqual(json.loads(move['parameters_json'])['target'], current['entity_id'])
