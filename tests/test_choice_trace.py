import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('choice_trace', Path(__file__).resolve().parents[1] / 'tools/choice_trace.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ChoiceTraceTests(unittest.TestCase):
    def trace(self, rows):
        text = '\n'.join(json.dumps(r, ensure_ascii=False) for r in rows)
        with patch.object(Path, 'read_text', return_value=text):
            return module.trace(Path('fixture.jsonl'))

    def offer(self):
        payload = {'player': {'hp': 30}, 'hand_count': 3, 'hidden_hand_count': 1,
                   'candidate_cards': [{'index': 0, 'id': 'STRIKE'}, {'index': 2, 'id': 'DEFEND'}],
                   'plans': [{'id': 'P1', 'sequence': [{'card_index': 0}, {'card_index': 2}],
                              'confidence': 'unverified', 'end_turn': False}]}
        return {'kind': 'model_input', 'stage': 'candidate_choice',
                'prompt': '局面與候選：' + json.dumps(payload)}

    def test_full_state_text_mentioning_p1_is_not_candidate_mode(self):
        result = self.trace([{'kind': 'model_input', 'stage': 'full_state', 'prompt': '選 P1'},
                             {'kind': 'model', 'decision': {'advice': '採用 P1'}}])
        self.assertEqual(result['candidate_calls'], 0)

    def test_selected_sequence_is_compared_by_card_identity_after_index_shift(self):
        rows = [self.offer(),
                {'kind': 'model_call', 'answer': {'choice': 'P1'}},
                {'kind': 'model', 'decision': {'candidate_id': 'P1'}},
                {'kind': 'state', 'state': {'player': {'hand': [{'index': 0, 'id': 'STRIKE'}]}}},
                {'kind': 'post', 't': 1, 'body': {'action': 'play_card', 'card_index': 0}, 'result': {'status': 'ok'}},
                {'kind': 'state', 'state': {'player': {'hand': [{'index': 1, 'id': 'DEFEND'}]}}},
                {'kind': 'post', 't': 2, 'body': {'action': 'play_card', 'card_index': 1}, 'result': {'status': 'ok'}}]
        result = self.trace(rows)
        self.assertEqual(result['accepted_calls'], 1)
        self.assertTrue(result['calls'][0]['sequence_matches'])
        self.assertFalse(result['calls'][0]['has_full_hand'])
        rows[-2]['state']['player']['hand'][0]['id'] = 'OTHER_CARD'
        self.assertFalse(self.trace(rows)['calls'][0]['sequence_matches'])

    def test_rejected_offer_does_not_count_full_followup_actions_as_selected_sequence(self):
        result = self.trace([self.offer(), {'kind': 'model_call', 'answer': {'choice': 'reject'}},
                            {'kind': 'model_input', 'stage': 'full_state'},
                            {'kind': 'model', 'decision': {'advice': '採用 P1'}},
                            {'kind': 'post', 't': 1, 'body': {'action': 'end_turn'}, 'result': {'status': 'ok'}}])
        self.assertEqual(result['rejected_calls'], 1)
        self.assertEqual(result['accepted_calls'], 0)
        self.assertEqual(result['calls'][0]['posts'], [])

    def test_wrong_target_failed_post_or_intervening_model_never_counts_as_complete(self):
        offer = self.offer()
        payload = json.loads(offer['prompt'].split('局面與候選：')[1])
        payload['plans'][0]['sequence'] = [{'card_index': 0, 'target': 'a'}]
        offer['prompt'] = '局面與候選：' + json.dumps(payload)
        rows = [offer, {'kind': 'model_call', 'answer': {'choice': 'P1'}},
                {'kind': 'model', 'decision': {'candidate_id': 'P1'}},
                {'kind': 'state', 'state': {'player': {'hand': [{'index': 0, 'id': 'STRIKE'}]}}},
                {'kind': 'post', 't': 1, 'body': {'action': 'play_card', 'card_index': 0, 'target': 'a'},
                 'result': {'status': 'ok'}}]
        self.assertTrue(self.trace(rows)['calls'][0]['completed_without_reasking'])
        rows[-1]['body']['target'] = 'b'
        self.assertFalse(self.trace(rows)['calls'][0]['sequence_matches'])
        rows[-1]['body']['target'] = 'a'
        rows[-1]['result']['status'] = 'error'
        self.assertFalse(self.trace(rows)['calls'][0]['sequence_matches'])
        rows[-1]['result']['status'] = 'ok'
        rows.insert(-1, {'kind': 'model_input', 'stage': 'full_state'})
        self.assertEqual(self.trace(rows)['calls'][0]['posts'], [])
