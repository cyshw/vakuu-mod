from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vakuu'))
from combat.facts import combat_facts


class SteamEruptionTests(unittest.TestCase):
    def state(self, status):
        return {'player': {'hp': 27, 'max_hp': 80, 'block': 0, 'hand': []}, 'battle': {'enemies': [
            {'entity_id': 'WATERFALL_GIANT_0', 'hp': 4, 'block': 0, 'status': status,
             'intents': [{'type': 'Attack', 'label': '10'}]}]}}

    def test_blast_and_block_needed_to_survive_the_kill(self):
        steam = combat_facts(self.state([{'id': 'STEAM_ERUPTION_POWER', 'amount': 48},
                                         {'id': 'WEAK_POWER', 'amount': 1}]))['steam_eruption'][0]
        self.assertEqual((steam['blast_if_killed_now'], steam['block_needed_next_turn_if_killed_now'],
                          steam['grows_each_turn'], steam['enemy_weak_now']), (48, 22, 3, True))

    def test_absent_without_the_power(self):
        self.assertNotIn('steam_eruption', combat_facts(self.state([])))


if __name__ == '__main__':
    unittest.main()
