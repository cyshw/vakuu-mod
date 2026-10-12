import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'vakuu'))
from combat import facts
from takeover import plans
from core import rules

SUITE=Path(__file__).resolve().parent/'regression'

class RegressionStates(unittest.TestCase):
    """The recorded exam states (tools/regression.py) must still go through the program without a model."""
    def test_program_handles_every_recorded_state(self):
        cases=[json.loads(p.read_text(encoding='utf-8')) for p in sorted(SUITE.glob('*.json'))]
        self.assertTrue(cases)
        for case in cases:
            with self.subTest(case['id']):
                state=case['state']
                rules.fingerprint(state)
                plans.auto_decision(state)
                if state.get('state_type') in rules.COMBAT:
                    result=facts.combat_facts(state)
                    self.assertGreaterEqual(result['block_needed_for_no_damage'],0)
                    facts.pile_contents(state.get('player') or {})

if __name__=='__main__':
    unittest.main()
