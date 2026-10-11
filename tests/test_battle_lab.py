import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('battle_lab', Path(__file__).resolve().parents[1] / 'tools/battle_lab.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


class BattleLabTests(unittest.TestCase):
    def event(self, index=0, attempt='a' * 32):
        return {'index': index, 'attempt': attempt, 'kind': 'before_action',
                'action': {'action': 'play_card', 'card_id': 'STRIKE_IRONCLAD', 'card_index': 1,
                           'target_combat_id': 7},
                'state': {'player': {'hand': [{'id': 'DEFEND_IRONCLAD'}, {'id': 'STRIKE_IRONCLAD'}]},
                          'battle': {'enemies': [{'combat_id': 7, 'entity_id': 'BEE_0'}]}}}

    def test_archive_deduplicates_recovers_and_separates_random_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = lab.Archive(folder)
            status = {'attempt': 'a' * 32, 'events': [self.event()]}
            archive.consume(status)
            archive.consume(status)
            archive = lab.Archive(folder)
            archive.consume(status)
            self.assertEqual(len(archive.path.read_text().splitlines()), 1)
            archive.consume({'attempt': 'b' * 32, 'events': [self.event(attempt='b' * 32)]})
            self.assertEqual(len(list(Path(folder).glob('*.jsonl'))), 2)

    def test_missing_event_and_unsafe_attempt_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = lab.Archive(folder)
            with self.assertRaises(ValueError):
                archive.consume({'attempt': '../escape', 'events': []})
            with self.assertRaises(ValueError):
                archive.consume({'attempt': 'a' * 32, 'events': [self.event(2)]})

    def test_card_identity_and_target_are_checked(self):
        event = self.event()
        decision = lab.human_decision(event)
        self.assertEqual(json.loads(decision['parameters_json'])['target'], 'BEE_0')
        other = dict(decision, parameters_json='{"card_index":1,"target":"BEE_1"}')
        self.assertFalse(lab.same_first_action(decision, other))
        event['state']['player']['hand'][1]['id'] = 'BASH'
        with self.assertRaises(ValueError): lab.human_decision(event)
        event = self.event()
        event['action']['target_combat_id'] = 8
        with self.assertRaises(ValueError): lab.human_decision(event)

    def test_only_completed_actions_compared_no_automatic_gold(self):
        calls = []
        def decide(config, state):
            calls.append(config)
            return {'action': 'end_turn', 'parameters_json': '{}'}
        rows = [self.event(0), self.event(1),
                {'kind': 'after_action', 'action': {'before_index': 1, 'status': 'Finished'}}]
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {'replay_eval': types.SimpleNamespace(decide=decide)}):
            path = Path(folder) / 'attempt.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
            output = lab.compare(path, 'claude:haiku@low', 1)
            report = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(calls, ['claude:haiku@low'])
        self.assertEqual(report[0]['event'], 1)
        self.assertTrue(report[0]['different'])
        self.assertFalse(report[0]['approved'])


if __name__ == '__main__': unittest.main()
