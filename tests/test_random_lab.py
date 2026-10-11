import unittest
from tools.random_lab import difficulty_weight, encounter_groups, sample_pool


class RandomLabTests(unittest.TestCase):
    def test_multiple_floors_do_not_add_weight_to_same_encounter(self):
        pool = [{'spec': {'encounter': name}, 'weight': 1} for name in
                ('CORPSE_SLUGS_WEAK', 'CORPSE_SLUGS_NORMAL', 'HAUNTED_SHIP_NORMAL')]
        groups = encounter_groups(pool)
        self.assertEqual(set(groups), {'CORPSE_SLUGS', 'HAUNTED_SHIP'})
        class CaptureRandom:
            def choices(self, names, weights, k):
                self.weights = weights
                return ['CORPSE_SLUGS']
            def choice(self, candidates): return candidates[-1]
        rng = CaptureRandom()
        chosen = sample_pool(pool, rng=rng)
        self.assertEqual(rng.weights, [1, 1])
        self.assertEqual(chosen['spec']['encounter'], 'CORPSE_SLUGS_NORMAL')

    def test_grindy_fights_have_more_weight_but_easy_fights_still_eligible(self):
        easy = difficulty_weight(80, 80, 0, 3)
        hard = difficulty_weight(35, 80, 22, 6)
        self.assertEqual(easy, 1)
        self.assertEqual(hard, 6)
        self.assertGreater(hard, easy)

    def test_each_proxy_increases_weight_without_requiring_all_three(self):
        base = difficulty_weight(80, 80, 0, 3)
        for inputs in ((35, 80, 0, 3), (80, 80, 22, 3), (80, 80, 0, 6)):
            self.assertGreater(difficulty_weight(*inputs), base)
