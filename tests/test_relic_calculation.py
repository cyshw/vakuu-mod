import copy
import unittest

from test_search import state, card, enemy
from combat.search import search_turn
from combat.relics import ENTRY_RELICS, CONDITIONAL_RELICS, turn_end_defense, compact_relics
from combat.facts import combat_facts


def relic(key):
    return {'id':key, 'description':{**ENTRY_RELICS, **CONDITIONAL_RELICS}[key], 'counter':None}


def plating(amount=4):
    return {'id':'PLATING_POWER', 'amount':amount,
            'description':f'在你的回合结束时获得{amount}点格挡。覆甲会在你的回合开始时减少1层。'}


class RelicCalculationTests(unittest.TestCase):
    def test_completed_acquisition_effects_are_not_applied_twice(self):
        s = state([card(0,damage=7)], [enemy(hp=20,attack=12)], energy=1)
        s['player']['block'] = 10
        s['player']['status'] = [{'id':'STRENGTH_POWER','amount':1}]
        s['player']['relics'] = [relic(k) for k in ('ARCANE_SCROLL','ANCHOR','VAJRA','WAR_PAINT','FRAGRANT_MUSHROOM')]
        result = search_turn(s)
        self.assertEqual(result['predicted_enemy_hp'], [13])
        self.assertEqual(result['predicted_hp_loss'], 2)

    def test_plating_is_unpowered_block_and_blur_retains_surplus(self):
        s = state([card(0,block=5)], [enemy(hp=20,attack=8)], energy=1)
        s['player']['status'] = [plating(), {'id':'FRAIL_POWER','amount':1},
            {'id':'BLUR_POWER','amount':1,'description':'你的下1个回合开始时格挡不会消失。'}]
        s['player']['relics'] = [relic('GORGET'), relic('DIAMOND_DIADEM')]
        result = search_turn(s,include_candidates=True)
        self.assertEqual(result['predicted_hp_loss'], 0)
        selected = next(p for p in result['candidates'] if p['sequence'])
        self.assertEqual(selected['end_turn_relic_block'], 4)
        self.assertEqual(selected['retained_block_next_turn'], 1)
        self.assertEqual(combat_facts(s)['block_needed_for_no_damage'], 4)

    def test_cloak_clasp_counts_unplayed_cards_before_flush(self):
        s = state([card(0,block=1), card(1,block=1)], [enemy(hp=30,attack=2)], energy=1)
        s['player']['relics'] = [relic('CLOAK_CLASP')]
        result = search_turn(s,include_candidates=True)
        self.assertEqual(result['predicted_hp_loss'], 0)
        self.assertTrue(all(p['end_turn_relic_block'] == 2-len(p['sequence']) for p in result['candidates']))
        self.assertEqual(turn_end_defense(s['player'])['cloak_clasp_block'], 2)

    def test_mummified_hand_is_inactive_for_supported_basic_cards(self):
        s = state([card(0)], [enemy(hp=20)], energy=1)
        s['player']['relics'] = [relic('MUMMIFIED_HAND')]
        self.assertEqual(search_turn(s)['reason'],'supported_turn')

    def test_top_only_blocks_when_supported_moves_can_empty_hand(self):
        s = state([card(0,cost=1),card(1,cost=1)], [enemy(hp=30)], energy=1)
        s['player']['relics'] = [relic('UNCEASING_TOP')]
        self.assertEqual(search_turn(s)['reason'],'supported_turn')
        s['player']['energy'] = 2
        self.assertEqual(search_turn(s)['reason'],'unmodeled_hand_empty_draw')

    def test_puzzle_draw_is_not_faked_when_hp_would_be_lost(self):
        s = state([card(0)], [enemy(hp=30,attack=8)], energy=1)
        s['player']['relics'] = [relic('CENTENNIAL_PUZZLE')]
        result = search_turn(s,include_candidates=True)
        self.assertEqual(result['reason'],'unmodeled_damage_draw')
        self.assertTrue(all(p['confidence']=='partial' for p in result['candidates']))
        s['player']['hand'] = [card(0,block=8)]
        self.assertEqual(search_turn(s)['reason'],'supported_turn')

    def test_unknown_modified_effect_keeps_fallback_and_original_prose(self):
        s = state();s['player']['relics']=[relic('GORGET')]
        s['player']['relics'][0]['description'] += '每打出一张牌失去1点生命。'
        self.assertEqual(search_turn(s)['reason'],'unknown_relic')
        payload = copy.deepcopy(s)
        compact_relics(payload,s)
        self.assertEqual(payload,s)
        s['player']['relics']=[relic('GORGET')]
        before = copy.deepcopy(s)
        payload=copy.deepcopy(s);compact_relics(payload,s)
        self.assertNotIn('description',payload['player']['relics'][0])
        self.assertEqual(s,before)

    def test_modified_plating_is_not_guessed(self):
        s=state();s['player']['status']=[plating()]
        s['player']['status'][0]['description']+='同时失去4点生命。'
        self.assertEqual(search_turn(s)['reason'],'unknown_end_turn_effect')
