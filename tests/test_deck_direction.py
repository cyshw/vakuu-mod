import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vakuu'))
from deck.direction import card_roles, deck_direction
from deck import advisor
from takeover import plans
from test_advisor import sample, METRICS


def card(cid, text, kind='Skill', index=0, cost='1'):
    return {'id': cid, 'description': text, 'type': kind, 'index': index, 'cost': cost}


class DeckDirectionTests(unittest.TestCase):
    def test_early_starters_do_not_establish_an_archetype(self):
        s = {'run': {'act': 1}, 'player': {'deck': [
            card('STRIKE_DEFECT', '造成6点伤害。', 'Attack') for _ in range(4)] + [
            card('ZAP', '生成1个闪电充能球。'), card('DEFEND_DEFECT', '获得5点格挡。')]}}
        context, _ = deck_direction(s)
        self.assertEqual(context['directions'], [])
        self.assertTrue(context['protect_starter_damage'])
        self.assertIn('damage', context['needs'])

    def test_focus_orb_deck_can_support_non_attack_damage_and_fits_offer(self):
        deck = [card('ZAP', '生成1个闪电充能球。'),
                card('COOLHEADED', '生成1个冰霜充能球。 抽1张牌。'),
                card('DEFRAGMENT', '获得2点集中。', 'Power')]
        s = {'run': {'act': 1}, 'player': {'deck': deck}}
        offer = card('GLACIER', '获得7点格挡。生成2个冰霜充能球。', index=5)
        before = copy.deepcopy(s)
        context, fits = deck_direction(s, [offer])
        self.assertFalse(context['protect_starter_damage'])
        self.assertEqual(context['directions'][0]['stage'], '已有配合')
        self.assertEqual(fits[5]['synergies'], ['現有充能球與集中配合'])
        self.assertEqual(fits[5]['fills_needs'], ['block'])
        self.assertEqual(context['cost_curve'], {'1': 3})
        self.assertEqual(s, before)

    def test_losing_focus_does_not_count_as_focus_scaling(self):
        self.assertNotIn('focus', card_roles(card('REPROGRAM', '失去2点集中。获得2点力量。')))
        self.assertNotIn('focus', card_roles(card('FOCUSED_STRIKE', '造成9点伤害。在本回合获得1点集中。', 'Attack')))
        self.assertIn('draw', card_roles(card('COMPILE_DRIVER', '你每有一种不同的充能球，就抽一张牌。')))

    def test_recorded_reward_exposes_both_damage_and_block_choices(self):
        s = json.loads(Path(__file__).with_name('deck_reward_floor3.json').read_text(encoding='utf-8'))
        context, fits = deck_direction(s, s['card_reward']['cards'])
        self.assertIn('damage', context['needs'])
        self.assertTrue(context['protect_starter_damage'])
        by_id = {c['id']: fits[c['index']] for c in s['card_reward']['cards']}
        self.assertIn('block', by_id['LEAP']['fills_needs'])
        self.assertIn('damage', by_id['BALL_LIGHTNING']['fills_needs'])
        self.assertIn('draw', by_id['SWEEPING_BEAM']['roles'])

    def test_payoff_without_support_is_incomplete_and_unknown_is_explicit(self):
        s = {'player': {'deck': [card('ALL_FOR_ONE', '造成10点伤害。', 'Attack', cost='2'),
                                  {'id': 'UNKNOWN'}]}}
        context, fits = deck_direction(s, [{'id': 'MOD_CARD', 'index': 8}])
        self.assertEqual(context['directions'][0]['stage'], '尚缺配合')
        self.assertEqual(context['unknown_description_count'], 1)
        self.assertFalse(fits[8]['effect_known'])
        self.assertEqual(fits[8]['roles'], [])

    def test_channel_cards_suggest_a_direction_without_claiming_completed_scaling(self):
        s = {'run': {'act': 1}, 'player': {'deck': [
            card('ZAP', '生成1个闪电充能球。'),
            card('COLD_SNAP', '造成6点伤害。生成1个冰霜充能球。', 'Attack'),
            card('COMPILE_DRIVER', '造成7点伤害。你每有一种不同的充能球，就抽一张牌。', 'Attack')]}}
        context, fits = deck_direction(s, [card('COOLHEADED', '生成1个冰霜充能球。抽1张牌。', index=9)])
        self.assertEqual(context['directions'][0]['stage'], '可發展，仍缺成長')
        self.assertTrue(context['protect_starter_damage'])
        self.assertEqual(len(fits[9]['synergies']), 1)

    def test_community_ranking_keeps_deck_fit_and_skip_option(self):
        with patch.object(advisor, 'codex_metrics', return_value=METRICS):
            advice = advisor.advise(sample())
        self.assertEqual(advice['engine'], 'Spire Codex')
        self.assertTrue(advice['can_skip'])
        self.assertFalse(advice['skip_evaluated'])
        self.assertEqual([(c['index'], c['score']) for c in advice['ranked_cards']],
                         [(7, 75), (9, 75), (2, 40)])
        self.assertTrue(all('deck_fit' in c for c in advice['ranked_cards']))

    def test_auto_pick_requires_deck_fit_and_defers_conflicting_needs(self):
        rating = {'status': 'ok', 'deck_direction': {'unknown_description_count': 0}, 'ranked_cards': [
            {'index': 4, 'name': 'A', 'score': 80, 'deck_fit': {'effect_known': True, 'fills_needs': ['damage']}},
            {'index': 7, 'name': 'B', 'score': 30, 'deck_fit': {'effect_known': True}}]}
        state = {'state_type': 'card_reward'}
        with patch.object(plans, 'advise', return_value=rating):
            self.assertEqual(plans.auto_decision(state)['action'], 'select_card_reward')
            rating['ranked_cards'][1]['deck_fit']['fills_needs'] = ['block']
            self.assertIsNone(plans.auto_decision(state))
            rating['ranked_cards'][1]['deck_fit']['fills_needs'] = []
            rating['ranked_cards'][0]['deck_fit']['fills_needs'] = []
            self.assertIsNone(plans.auto_decision(state))


if __name__ == '__main__': unittest.main()
