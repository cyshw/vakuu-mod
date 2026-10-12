import copy
import unittest

from tools.batch_lab import verify_initial


class InitialConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.spec = {'character': 'DEFECT', 'deck': [{'id': 'ECHO_FORM', 'upgrades': 1}],
                     'relics': ['CRACKED_CORE'], 'hp': 70, 'max_hp': 70}
        self.state = {'player': {'character_id': 'DEFECT',
                                'deck': [{'id': 'ECHO_FORM', 'is_upgraded': True}],
                                'relics': [{'id': 'CRACKED_CORE'}], 'hp': 70, 'max_hp': 70},
                      'battle': {'enemies': [{'entity_id': 'KNIGHT_0'}]}}

    def test_live_configuration_must_match_before_model_can_run(self):
        verify_initial(self.spec, self.state)
        for key, value in [('character_id', 'IRONCLAD'),
                           ('deck', [{'id': 'ECHO_FORM', 'is_upgraded': False}]),
                           ('relics', []), ('hp', 69)]:
            changed = copy.deepcopy(self.state)
            changed['player'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_initial(self.spec, changed)

    def test_missing_encounter_and_unverifiable_upgrade_are_rejected(self):
        changed = copy.deepcopy(self.state)
        changed['battle']['enemies'] = []
        with self.assertRaises(ValueError):
            verify_initial(self.spec, changed)
        self.spec['deck'][0]['upgrades'] = 2
        with self.assertRaises(ValueError):
            verify_initial(self.spec, self.state)
