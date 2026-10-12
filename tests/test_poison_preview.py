import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vakuu'))
from combat.facts import combat_facts, compact_poison, poison_preview
from core.game_api import compact


class PoisonPreviewTests(unittest.TestCase):
    def state(self, preview=True):
        poison = {'id': 'POISON_POWER', 'amount': 5, 'description': 'Poison explanation ' * 25,
                  'keywords': [{'name': 'Poison', 'description': 'Repeated explanation'}]}
        if preview: poison['next_turn_hp_loss'] = 9
        return {'player': {'hp': 30}, 'battle': {'enemies': [
            {'entity_id': 'ENEMY_0', 'hp': 8, 'block': 100, 'status': [poison,
             {'id': 'SPECIAL_POWER', 'description': 'Must retain unusual death rules'}]}]}}

    def test_uses_game_preview_instead_of_stacks_or_block(self):
        self.assertEqual(poison_preview(self.state()), [{'enemy': 'ENEMY_0', 'stacks': 5,
                         'next_own_turn_hp_loss': 9, 'hp_if_only_poison': 0}])

    def test_numeric_replacement_is_smaller_and_keeps_other_rules_and_raw_state(self):
        raw = self.state()
        unchanged = copy.deepcopy(raw)
        small = compact(raw)
        small['combat_facts'] = combat_facts(raw)
        before = len(json.dumps(small))
        compact_poison(small, raw)
        self.assertLess(len(json.dumps(small)), before)
        self.assertEqual(small['battle']['enemies'][0]['status'], raw['battle']['enemies'][0]['status'][1:])
        self.assertEqual(raw, unchanged)

    def test_old_mod_and_invalid_preview_keep_original_poison_rules(self):
        for value in (None, True, -1):
            raw = self.state(False)
            if value is not None: raw['battle']['enemies'][0]['status'][0]['next_turn_hp_loss'] = value
            small = compact(raw)
            small['combat_facts'] = combat_facts(raw)
            compact_poison(small, raw)
            self.assertEqual(small['battle']['enemies'][0]['status'], raw['battle']['enemies'][0]['status'])
            self.assertEqual(poison_preview(raw), [])
