import unittest
from tools.random_lab import difficulty_weight


class RandomLabTests(unittest.TestCase):
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
