import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'outputs'))
import spire_quota as q

class Tests(unittest.TestCase):
    def test_missing_is_not_zero_and_multiple_buckets_survive(self):
        result={'rateLimitsByLimitId':{'first':{'primary':{'usedPercent':25,'windowDurationMins':300,'resetsAt':0}},
                                      'second':{'secondary':{'usedPercent':100,'windowDurationMins':10080}}}}
        value=q.format_limits(result)
        self.assertIn('剩餘 75%',value)
        self.assertIn('剩餘 0%',value)
        self.assertIn('7 天',value)
        self.assertNotIn('%',q.format_limits({'rateLimits':{'primary':None}}))

    def test_claude_does_not_query_codex(self):
        with patch.object(q,'read_codex_limits') as read:
            self.assertEqual(q.quota('claude:haiku')['provider'],'claude')
        read.assert_not_called()

    def test_cached_read_does_not_repeat_request(self):
        with patch.object(q,'_cache',{}), patch.object(q,'read_codex_limits',return_value={}) as read:
            q.quota('codex:'); q.quota('codex:gpt-6-sol')
        self.assertEqual(read.call_count,1)
