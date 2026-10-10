import copy
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'vakuu'))
import spire_codex
import sts2_advisor
from sts2_advisor import advise

def card(key, name, index=0, upgraded=False):
    return dict(id=key, name=name, index=index, is_upgraded=upgraded)

def sample():
    return {
        'state_type': 'card_reward',
        'player': {
            'character_id': 'IRONCLAD', 'character': '鐵甲戰士',
            'deck': [card('CORRUPTION', '腐化'), card('FEEL_NO_PAIN', '無懼疼痛'),
                     card('STRIKE_IRONCLAD', '打擊'), card('STRIKE_IRONCLAD', '打擊')],
            'relics': [{'id': 'BURNING_BLOOD', 'name': '燃燒之血'}],
        },
        'card_reward': {'can_skip': True, 'cards': [
            card('DARK_EMBRACE', '黑暗之擁', 2),
            card('BODY_SLAM', '全身撞擊', 7),
            card('BODY_SLAM', '全身撞擊+', 9, True)]}
    }

class AdapterTests(unittest.TestCase):
    # These cover the offline BoberInSpire fallback; never touch the network.
    def setUp(self):
        patcher = patch.object(sts2_advisor, 'codex_metrics', return_value=None)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_shop_cards_are_scored_with_shop_indices(self):
        state = sample()
        state['state_type'] = 'shop'
        state['shop'] = {'items': [
            {'index': 0, 'category': 'card', 'is_stocked': True, 'card_id': 'DARK_EMBRACE', 'card_name': '黑暗之擁'},
            {'index': 1, 'category': 'card', 'is_stocked': False, 'card_id': 'BODY_SLAM', 'card_name': '全身撞擊'},
            {'index': 5, 'category': 'relic', 'is_stocked': True, 'relic_name': '遺物'},
            {'index': 6, 'category': 'card', 'is_stocked': True, 'card_id': 'BODY_SLAM', 'card_name': '全身撞擊'}]}
        result = advise(state)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(sorted(c['index'] for c in result['ranked_cards']), [0, 6])  # Sold-out and relics skipped.

    def test_localized_ids_duplicates_upgrades_and_indices(self):
        state = sample()
        original = copy.deepcopy(state)
        result = advise(state)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['deck_count'], 4)
        self.assertEqual(result['archetype'], 'exhaust')
        rows = {r['index']: r for r in result['ranked_cards']}
        self.assertEqual(set(rows), {2, 7, 9})
        self.assertEqual(rows[2]['english_name'], 'Dark Embrace')
        self.assertEqual(rows[9]['english_name'], 'Body Slam+')
        self.assertGreater(rows[9]['score'], rows[7]['score'])
        self.assertEqual(rows[9]['name'], '全身撞擊+')
        self.assertEqual(state, original)
        self.assertFalse(result['skip_evaluated'])

    def test_missing_deck_does_not_guess(self):
        state = sample()
        del state['player']['deck']
        self.assertEqual(advise(state)['status'], 'missing_deck_api')

    def test_unknown_mod_card_disables_best_pick(self):
        state = sample()
        state['card_reward']['cards'].append(card('MOD_UNKNOWN_CARD', '模組牌', 4))
        result = advise(state)
        self.assertEqual(result['status'], 'incomplete_data')
        self.assertIsNone(result['best_scored_index'])
        self.assertEqual(result['unknown_items'][0]['id'], 'MOD_UNKNOWN_CARD')

    def test_menu_never_scores(self):
        self.assertEqual(advise({'state_type': 'menu'})['status'], 'not_card_reward')


METRICS = {'data_through': '2026-10-10', 'bracket': 'a10', 'character': 'IRONCLAD', 'baseline_win_rate': 30.0,
           'character_runs': 1000, 'cards': {'DARK_EMBRACE': {'score': 40, 'tier': 'B', 'win_rate': 31.0, 'lift': 1.0},
                                             'BODY_SLAM': {'score': 75, 'tier': 'A', 'win_rate': 38.0, 'lift': 5.5}}}

class CodexTests(unittest.TestCase):
    def test_codex_scores_rank_offers_and_keep_indices(self):
        with patch.object(sts2_advisor, 'codex_metrics', return_value=METRICS):
            result = advise(sample())
        self.assertEqual(result['engine'], 'Spire Codex')
        self.assertEqual([(c['index'], c['score']) for c in result['ranked_cards']], [(7, 75), (9, 75), (2, 40)])
        self.assertEqual(result['ranked_cards'][1]['name'], '全身撞擊+')

    def test_unrated_offer_is_incomplete_and_sorted_last(self):
        state = sample(); state['card_reward']['cards'].append(card('NEW_CARD', '新卡', 11))
        with patch.object(sts2_advisor, 'codex_metrics', return_value=METRICS):
            result = advise(state)
        self.assertEqual(result['status'], 'incomplete_data')
        self.assertIsNone(result['best_scored_index'])
        self.assertEqual(result['ranked_cards'][-1]['index'], 11)

    def test_cache_refreshes_daily_and_survives_outages(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory,              patch.object(spire_codex, 'CACHE', Path(directory)):
            with patch.object(spire_codex, 'fetch', return_value={'cards': {}}) as fetch:
                spire_codex.card_metrics('DEFECT', now=lambda: 1000)
                spire_codex.card_metrics('DEFECT', now=lambda: 1000 + 3600)  # Same day: cached.
            self.assertEqual(fetch.call_count, 1)
            with patch.object(spire_codex, 'fetch', side_effect=OSError('offline')):
                stale = spire_codex.card_metrics('DEFECT', now=lambda: 1000 + 2 * 86400)
            self.assertEqual(stale['fetched_at'], 1000)  # Yesterday's copy instead of nothing.
            with patch.object(spire_codex, 'fetch', side_effect=OSError('offline')):
                self.assertIsNone(spire_codex.card_metrics('SILENT', now=lambda: 1000))


SLUG = {'id': 'CORPSE_SLUG', 'name': '噬尸蛞蝓', 'min_hp': 25, 'max_hp': 27, 'min_hp_ascension': 27, 'max_hp_ascension': 29,
        'moves': [{'id': 'WHIP', 'name': '鞭打', 'intent': 'Attack', 'damage': {'normal': 3, 'ascension': None, 'hit_count': 2}},
                  {'id': 'GLOMP', 'name': '扑上', 'intent': 'Attack', 'damage': {'normal': 8, 'ascension': 9}}],
        'innate_powers': [{'power_id': 'RAVENOUS', 'amount': 4, 'amount_ascension': 5}],
        'attack_pattern': {'type': 'cycle', 'states': [{'id': 'W', 'type': 'move', 'move_id': 'WHIP', 'next': 'G'},
                                                       {'id': 'G', 'type': 'move', 'move_id': 'GLOMP', 'next': 'W'}]}}
RAVENOUS = {'name': '饥饿', 'description': '当有敌人死亡时，在本回合被[gold]击晕[/gold]然后获得[blue][Amount][/blue]点力量。'}

class MonsterIntelTests(unittest.TestCase):
    def test_ascension_values_pattern_and_cache(self):
        fake = lambda kind, entity_id: SLUG if kind == 'monsters' else RAVENOUS
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory,              patch.object(spire_codex, 'CACHE', Path(directory)), patch.object(spire_codex, '_get', side_effect=fake) as get:
            intel = spire_codex.monster_intel('CORPSE_SLUG', 9, now=lambda: 0)
            spire_codex.monster_intel('CORPSE_SLUG', 9, now=lambda: 3600)  # Cached within the week.
            low = spire_codex.monster_intel('CORPSE_SLUG', 0, now=lambda: 0)
        self.assertEqual(intel['hp_range'], [27, 29])
        self.assertEqual([m['damage'] for m in intel['moves']], ['3×2', 9])
        self.assertEqual(intel['abilities'][0]['description'], '当有敌人死亡时，在本回合被击晕然后获得5点力量。')
        self.assertEqual(intel['pattern']['steps'], ['鞭打 → 扑上', '扑上 → 鞭打'])
        self.assertEqual(low['hp_range'], [25, 27])
        self.assertEqual(get.call_count, 4)  # Monster + power, twice: A9 once (then cached) and A0.

if __name__ == '__main__':
    unittest.main()
