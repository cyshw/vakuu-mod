import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'vakuu'))
import spire_codex as codex
from vakuu_facts import route_ahead


class EventTests(unittest.TestCase):
    def test_multistep_event_keeps_following_choices(self):
        rows = [{'id': 'TEST', 'pages': [
            {'id': 'START', 'options': [{'id': 'PAY', 'title': '[b]支付[/b]'}]},
            {'id': 'PAY', 'options': [{'id': 'TAKE', 'title': '拿牌', 'description': '失去7生命'}]}]}]
        self.assertEqual(codex._events(rows)['TEST']['支付']['next'], ['拿牌：失去7生命'])

    def test_locked_options_are_not_advised(self):
        def cached(name, *args):
            if name == 'events-zhs': return {'TEST': {'支付': {'id': 'PAY', 'next': ['拿牌']}}}
            return {'events': {'TEST': {'PAY': {'win_rate': 0.5}}}}
        with patch.object(codex, '_cached', side_effect=cached):
            result = codex.event_advice('TEST', [
                {'index': 3, 'title': '支付'}, {'index': 7, 'title': '支付', 'is_locked': True}], 'DEFECT')
        self.assertEqual(result, [{'index': 3, 'title': '支付', 'then_choose_from': ['拿牌'],
                                   'community': {'win_rate': 0.5}}])

    def test_event_cache_retains_stale_data_when_offline(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, patch.object(codex, 'CACHE', Path(directory)):
            (Path(directory) / 'event.json').write_text('{"fetched_at":0,"data":{"events":{}}}')
            with patch.object(codex.urllib.request, 'urlopen', side_effect=OSError('offline')):
                self.assertEqual(codex._cached('event', 'https://example.test', 1, lambda x: x), {'events': {}})

    def test_route_distances_use_remaining_path(self):
        route = {'path': [(1, 2), (2, 3), (1, 4)],
                 'types': {(1, 2): 'Monster', (2, 3): 'RestSite', (1, 4): 'Elite'}}
        result = route_ahead(route)
        self.assertEqual(result['rooms_to_rest_site'], 2)
        self.assertEqual(result['rooms_to_elite'], 3)
        self.assertIsNone(result['rooms_to_shop'])
        self.assertIsNone(route_ahead(None))
