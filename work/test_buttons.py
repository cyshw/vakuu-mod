import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
path=Path(__file__).resolve().parent.parent/'outputs'/'spire-buttons.py'
spec=importlib.util.spec_from_file_location('buttons',path)
b=importlib.util.module_from_spec(spec); spec.loader.exec_module(b)

class Tests(unittest.TestCase):
    def test_optional_null_target_is_omitted(self):
        params={'card_index':1,'target':None}
        self.assertEqual(b.validate({'state_type':'monster'},'play_card',params),
                         {'action':'play_card','card_index':1})
        self.assertIn('target',params)  # Validation does not mutate model output.
        self.assertEqual(b.validate({'state_type':'monster'},'play_card',{'card_index':1,'target':''}),
                         {'action':'play_card','card_index':1})
        with self.assertRaises(ValueError):
            b.validate({'state_type':'monster'},'play_card',{'card_index':1,'target':0})

    def test_takeover_continues_into_next_player_turn(self):
        player1={'state_type':'monster','battle':{'round':1,'turn':'player','is_play_phase':True}}
        enemy={'state_type':'monster','battle':{'round':1,'turn':'enemy','is_play_phase':False}}
        player2={'state_type':'monster','battle':{'round':2,'turn':'player','is_play_phase':True}}
        decision={'advice':'結束回合','stop':False,'action':'end_turn','parameters_json':'{}'}
        c=b.Controller(lambda message: None)
        # GET, stale-state check, POST, then enemy polling and next round.
        responses=[player1,player1,{'status':'ok'},enemy,enemy,player2,player2,{'status':'ok'}]
        with patch.object(b.api,'request',side_effect=responses) as api, patch.object(c,'decide',return_value=decision) as decide, patch.object(c.stop_event,'wait',return_value=False):
            c.run(True,2)
        self.assertEqual(decide.call_count,2)
        self.assertEqual(decide.call_args_list[1].args[0],player2)
        self.assertEqual(sum(len(call.args)==2 for call in api.call_args_list),2)

    def test_stop_while_waiting_never_calls_model_or_posts(self):
        enemy={'state_type':'boss','battle':{'turn':'enemy','is_play_phase':False}}
        c=b.Controller(lambda message: None)
        with patch.object(b.api,'request',return_value=enemy) as api, patch.object(c,'decide') as decide, patch.object(c.stop_event,'wait',return_value=True):
            c.run(True)
        decide.assert_not_called()
        self.assertTrue(all(len(call.args)==1 for call in api.call_args_list))

    def test_advice_never_posts(self):
        state={'state_type':'event','event':{'event_id':'NEOW'}}
        c=b.Controller(lambda message: None)
        with patch.object(b.api,'request',return_value=state) as api, patch.object(c,'decide',return_value={'advice':'建議','stop':False,'action':'choose_event_option','parameters_json':'{"index":0}'}):
            c.run(False)
        self.assertTrue(all(len(call.args)==1 for call in api.call_args_list))

    def test_stale_state_blocks_action(self):
        before={'state_type':'event','event':{'options':[0,1]}}
        after={'state_type':'event','event':{'options':[1]}}
        c=b.Controller(lambda message: None)
        with patch.object(b.api,'request',side_effect=[before,after]) as api, patch.object(c,'decide',return_value={'advice':'建議','stop':False,'action':'choose_event_option','parameters_json':'{"index":0}'}):
            c.run(True,1)
        self.assertTrue(all(len(call.args)==1 for call in api.call_args_list))

    def test_dangerous_or_wrong_screen_actions_rejected(self):
        for action,params in [('menu_select',{'option':'abandon_run'}),('play_card',{'card_index':0}),('choose_event_option',{'index':True}),('choose_event_option',{'index':-1}),('choose_event_option',{'index':1,'extra':'x'})]:
            with self.assertRaises(ValueError): b.validate({'state_type':'event'},action,params)

    def test_valid_action_uses_actual_index(self):
        self.assertEqual(b.validate({'state_type':'card_reward'},'select_card_reward',{'card_index':9}),{'action':'select_card_reward','card_index':9})

    def test_stop_after_model_prevents_post(self):
        c=b.Controller(lambda message: None)
        def decide(state):
            c.stop()
            return {'advice':'建議','stop':False,'action':'choose_event_option','parameters_json':'{"index":0}'}
        with patch.object(b.api,'request',return_value={'state_type':'event'}) as api, patch.object(c,'decide',side_effect=decide):
            c.run(True,1)
        self.assertTrue(all(len(call.args)==1 for call in api.call_args_list))

    def test_completed_card_selection_confirms_without_model(self):
        picking={'state_type':'card_select','card_select':{'preview_showing':False,'can_confirm':False,'cards':[{'index':0}]}}
        chosen={'state_type':'card_select','card_select':{'preview_showing':True,'can_confirm':True,'cards':[{'index':0}]}}
        decision={'advice':'移除打擊','stop':False,'action':'select_card','parameters_json':'{"index":0}'}
        c=b.Controller(lambda message: None)
        responses=[picking,picking,{'status':'ok'},chosen,chosen,{'status':'ok'}]
        with patch.object(b.api,'request',side_effect=responses) as api, patch.object(c,'decide',return_value=decision) as decide, patch.object(c.stop_event,'wait',return_value=False), patch.object(c,'wait_for_change'):
            c.run(True,2)
        self.assertEqual(decide.call_count,1)
        posts=[call.args[1] for call in api.call_args_list if len(call.args)==2]
        self.assertEqual(posts,[{'action':'select_card','index':0},{'action':'confirm_selection'}])
        with self.assertRaises(ValueError):
            b.validate(chosen,'select_card',{'index':1})

    def test_prune_keeps_recent_runs_and_settings(self):
        import os, tempfile
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            runtime=Path(directory)/'button-runtime'; runtime.mkdir()
            (runtime/'model-settings.json').write_text('{}')
            for i in range(5):
                run=runtime/f'0000000{i}-0000-0000-0000-000000000000'; run.mkdir(); os.utime(run,(i,i))
            with patch.object(b,'ROOT',Path(directory)): b.prune_runs(keep=2)
            self.assertEqual(sorted(p.name[7] for p in runtime.iterdir() if p.is_dir()),['3','4'])
            self.assertTrue((runtime/'model-settings.json').exists())

    def test_auto_decisions_for_single_choice_screens(self):
        act=lambda state: (lambda d: d and (d['action'], b.json.loads(d['parameters_json'])))(b.auto_decision(state))
        player={'potions':[{'can_use_in_combat':True}],'max_potion_slots':2}
        rewards={'state_type':'rewards','player':player,'rewards':{'items':[{'index':0,'type':'card'},{'index':1,'type':'gold'}],'can_proceed':True}}
        self.assertEqual(act(rewards),('claim_reward',{'index':1}))
        full={**rewards,'player':{'potions':[{},{}],'max_potion_slots':2},'rewards':{'items':[{'index':0,'type':'potion'}],'can_proceed':True}}
        self.assertIsNone(act(full))  # A full belt needs a real choice.
        self.assertEqual(act({'state_type':'rewards','rewards':{'items':[],'can_proceed':True}}),('proceed',{}))
        self.assertEqual(act({'state_type':'map','map':{'next_options':[{'index':0}]}}),('choose_map_node',{'index':0}))
        self.assertIsNone(act({'state_type':'map','map':{'next_options':[{'index':0},{'index':1}]}}))
        self.assertEqual(act({'state_type':'event','event':{'in_dialogue':True}}),('advance_dialogue',{}))
        self.assertEqual(act({'state_type':'event','event':{'options':[{'index':0,'is_locked':True},{'index':1,'is_locked':False}]}}),
                         ('choose_event_option',{'index':1}))
        stuck={'state_type':'monster','player':{'hand':[{'can_play':False}],'potions':[]}}
        self.assertEqual(act(stuck),('end_turn',{}))
        self.assertIsNone(act({**stuck,'player':{'hand':[{'can_play':False}],'potions':[{'can_use_in_combat':True}]}}))
        self.assertIsNone(act({**stuck,'player':{'hand':[{'can_play':True}],'potions':[]}}))
        for state in (rewards,{'state_type':'map','map':{'next_options':[{'index':0}]}}):
            action,params=act(state); b.validate(state,action,params)

    def test_card_rewards_open_once_each(self):
        state=lambda n: {'state_type':'rewards','run':{'floor':3},'rewards':{'can_proceed':True,
                         'items':[{'index':i,'type':'card'} for i in range(n)]}}
        opened=set()
        self.assertEqual(b.auto_decision(state(2),opened)['action'],'claim_reward')
        self.assertEqual(b.auto_decision(state(1),opened)['action'],'claim_reward')  # First was taken.
        self.assertIsNone(b.auto_decision(state(1),opened))  # Skipped and still listed: let the model decide.
        self.assertIsNone(b.auto_decision(state(1)))  # Advice mode never auto-opens.

    def test_rest_site_waits_for_proceed_then_leaves(self):
        resting={'state_type':'rest_site','rest_site':{'options':[],'can_proceed':False}}
        ready={'state_type':'rest_site','rest_site':{'options':[],'can_proceed':True}}
        c=b.Controller(lambda message: None)
        with patch.object(b.api,'request',side_effect=[resting,ready]), patch.object(c.stop_event,'wait',return_value=False):
            self.assertEqual(c.wait_until_settled(resting),ready)
        self.assertEqual(b.auto_decision(ready)['action'],'proceed')
        self.assertIsNone(b.auto_decision({'state_type':'rest_site','rest_site':{'options':[{'index':0,'is_enabled':True}],'can_proceed':True}}))
        b.validate(ready,'proceed',{})

    def test_mid_animation_screens_are_waited_out(self):
        for state in ({'state_type':'treasure','treasure':{'message':'Opening chest...','can_proceed':True}},
                      {'state_type':'monster','message':'Combat ended. Waiting for rewards...'},
                      {'state_type':'shop','shop':{'items':[],'can_proceed':False,'error':'not ready'}}):
            self.assertTrue(b.transitional(state),state)
        for state in ({'state_type':'treasure','treasure':{'relics':[{'index':0}],'can_proceed':True}},
                      {'state_type':'monster','battle':{}},
                      {'state_type':'shop','shop':{'items':[{'index':0}],'can_proceed':True}}):
            self.assertFalse(b.transitional(state),state)

    def test_treasure_claims_single_relic_then_leaves(self):
        chest={'state_type':'treasure','treasure':{'relics':[{'index':0,'name':'磨刀石'}],'can_proceed':True}}
        decision=b.auto_decision(chest)
        self.assertEqual(decision['action'],'claim_treasure_relic')
        b.validate(chest,decision['action'],b.json.loads(decision['parameters_json']))
        self.assertEqual(b.auto_decision({'state_type':'treasure','treasure':{'relics':[],'can_proceed':True}})['action'],'proceed')
        self.assertIsNone(b.auto_decision({'state_type':'treasure','treasure':{'relics':[{'index':0},{'index':1}],'can_proceed':True}}))
        self.assertEqual(b.auto_decision({'state_type':'treasure','treasure':{'can_proceed':True}})['action'],'proceed')
        self.assertIsNone(b.auto_decision({'state_type':'treasure','treasure':{'message':'Opening chest...','can_proceed':True}}))

    @staticmethod
    def combat(hand, energy=3, round_=1, enemies=None, block=0, hp=40):
        return {'state_type':'boss','battle':{'round':round_,'turn':'player','is_play_phase':True,
                'enemies':enemies if enemies is not None else [{'entity_id':'BOSS_0','name':'頭目','hp':50,'block':0,
                          'intents':[{'type':'Attack','label':'12×2'},{'type':'Defend','label':''}]}]},
                'player':{'hp':hp,'block':block,'energy':energy,
                          'hand':[{'index':i,'name':n,'cost':'1','can_play':True,'target_type':'AnyEnemy' if '打' in n else 'Self'}
                                  for i,n in enumerate(hand)]}}

    def test_combat_facts_do_the_arithmetic(self):
        facts=b.combat_facts(self.combat(['打擊','防禦'],block=5,hp=19))
        self.assertEqual(facts['incoming_attack_damage'],24)
        self.assertEqual(facts['block_needed_for_no_damage'],19)
        self.assertTrue(facts['lethal_if_no_more_block'])
        self.assertEqual([c['name'] for c in facts['playable_cards']],['打擊','防禦'])
        self.assertEqual(b.incoming_damage({'type':'Buff','label':''}),0)
        self.assertEqual(b.incoming_damage({'type':'Attack','label':'21'}),21)

    def test_turn_plan_plays_without_asking_again(self):
        first=self.combat(['防禦','打擊','打擊+'])
        decision={'advice':'先防後攻','stop':False,'action':'play_card','parameters_json':'{"card_index":0}',
                  'next_cards':[{'hand_index':2,'target':'BOSS_0'},{'hand_index':1,'target':'BOSS_0'},{'hand_index':b.END_TURN,'target':''}]}
        second=self.combat(['打擊','打擊+'],energy=2)
        third=self.combat(['打擊'],energy=1)
        fourth=self.combat([],energy=0)
        c=b.Controller(lambda message: None)
        responses=[first,first,{'status':'ok'},second,second,{'status':'ok'},third,third,{'status':'ok'},fourth,fourth,{'status':'ok'}]
        with patch.object(b.api,'request',side_effect=responses) as api, patch.object(c,'decide',return_value=decision) as decide, patch.object(c.stop_event,'wait',return_value=False), patch.object(c,'wait_for_change'):
            c.run(True,4)
        self.assertEqual(decide.call_count,1)
        posts=[call.args[1] for call in api.call_args_list if len(call.args)==2]
        self.assertEqual(posts,[{'action':'play_card','card_index':0},{'action':'play_card','card_index':1,'target':'BOSS_0'},
                                {'action':'play_card','card_index':0,'target':'BOSS_0'},{'action':'end_turn'}])

    def test_plan_is_dropped_when_the_turn_surprises_it(self):
        first=self.combat(['快速檢索','打擊'])
        decision={'advice':'抽牌','stop':False,'action':'play_card','parameters_json':'{"card_index":0}',
                  'next_cards':[{'hand_index':1,'target':'BOSS_0'}]}
        self.assertIsNone(b.plan_decision(b.start_plan(first,decision),self.combat(['打擊','防禦'])))  # Drew a card.
        self.assertIsNone(b.plan_decision(b.start_plan(first,decision),self.combat(['打擊'],round_=2)))  # New turn.
        self.assertIsNone(b.plan_decision(b.start_plan(first,decision),self.combat(['打擊'],enemies=[])))  # Target died.
        self.assertIsNone(b.plan_decision(b.start_plan(first,decision),self.combat([])))  # Card is gone.
        self.assertIsNotNone(b.plan_decision(b.start_plan(first,decision),self.combat(['打擊'])))
        self.assertIsNone(b.start_plan({'state_type':'map'},decision))

    def test_auto_mode_routes_and_escalates(self):
        easy=self.combat(['打擊'],hp=40); easy['state_type']='monster'; easy['player']['max_hp']=40
        easy['battle']['enemies'][0]['intents']=[]
        hard=self.combat(['打擊'])  # Boss.
        self.assertFalse(b.hard_screen(easy)); self.assertTrue(b.hard_screen(hard))
        good={'advice':'打','stop':False,'action':'end_turn','parameters_json':'{}','next_cards':[]}
        gave_up={'advice':'不知道','stop':True,'action':'none','parameters_json':'{}','next_cards':[]}
        c=b.Controller(lambda message: None)
        auto={'model_key':'auto:','effort':'low'}
        def tiers(answers, state):
            calls=[]
            def fake(state_, selection): calls.append(selection['model_key']); return dict(answers[len(calls)-1])
            with patch.object(b.models,'settings',return_value=auto), patch.object(c,'decide',side_effect=fake):
                result=c.decide_routed(state)
            return calls, result
        self.assertEqual(tiers([good],easy)[0],['claude:haiku'])
        self.assertEqual(tiers([good],hard)[0],['claude:sonnet'])
        calls,result=tiers([gave_up,good],hard)
        self.assertEqual(calls,['claude:sonnet','claude:opus']); self.assertIn('Opus',result['advice'])
        calls,result=tiers([gave_up,gave_up],hard)
        self.assertEqual(len(calls),2); self.assertTrue(result['stop'])  # Opus giving up stops takeover.
        with patch.object(b.models,'settings',return_value={'model_key':'claude:haiku','effort':''}), patch.object(c,'decide',return_value=good) as decide:
            c.decide_routed(hard)
        self.assertEqual(decide.call_args.args[1]['model_key'],'claude:haiku')  # Manual choice is respected.

    def test_enchant_selection_without_preview_is_confirmed(self):
        pick=lambda n: {'state_type':'card_select','card_select':{'preview_showing':False,'can_confirm':True,
                        'selected_count':n,'min_select':1,'max_select':1,'cards':[{'index':0,'selected':bool(n)}]}}
        self.assertIsNone(b.auto_decision(pick(0)))
        self.assertEqual(b.auto_decision(pick(1))['action'],'confirm_selection')
        with self.assertRaises(ValueError): b.validate(pick(1),'select_card',{'index':0})
        self.assertFalse(b.selection_complete({'state_type':'card_select','card_select':{'selected_count':1,'max_select':3}}))

    def test_waits_until_action_shows_up_and_while_loading(self):
        old={'state_type':'map','map':{'current_position':{'row':2}}}
        moved={'state_type':'unknown'}
        c=b.Controller(lambda message: None)
        with patch.object(b.api,'request',side_effect=[old,old,moved]) as api, patch.object(c.stop_event,'wait',return_value=False):
            c.wait_for_change(old)
        self.assertEqual(api.call_count,3)
        self.assertTrue(b.transitional(moved))
        self.assertTrue(b.transitional({'state_type':'map','map':{'next_options':[]}}))
        self.assertFalse(b.transitional({'state_type':'map','map':{'next_options':[{'index':0}]}}))

    def test_plan_uses_game_names_and_piles_do_not_abort(self):
        hand=self.combat(['引雷针','防禦'])
        decision={'advice':'','stop':False,'action':'play_card','parameters_json':'{"card_index":1}',
                  'next_cards':[{'hand_index':0,'target':''},{'hand_index':9,'target':''}]}
        plan=b.start_plan(hand,decision)
        self.assertEqual([e['name'] for e in plan['queue']],['引雷针'])  # Out-of-range index ends the plan.
        before=dict(hand,player=dict(hand['player'],discard_pile=[],discard_pile_count=0))
        after=dict(hand,player=dict(hand['player'],discard_pile=[{'name':'黏液'}],discard_pile_count=1))
        self.assertEqual(b.fingerprint(before),b.fingerprint(after))
        self.assertNotEqual(b.fingerprint(hand),b.fingerprint(self.combat(['引雷针'])))

    def test_new_fight_waits_for_the_opening_hand(self):
        loading={'state_type':'unknown'}
        dealing={'state_type':'monster','battle':{'round':1,'turn':'player','is_play_phase':False},'player':{'hand':[]}}
        ready={'state_type':'monster','battle':{'round':1,'turn':'player','is_play_phase':True},'player':{'hand':[{'name':'打擊'}]}}
        c=b.Controller(lambda message: None)
        with patch.object(b.api,'request',side_effect=[dealing,ready]), patch.object(c.stop_event,'wait',return_value=False):
            state=c.wait_until_settled(c.wait_for_play_phase(c.wait_until_settled(loading)))
        self.assertEqual(state,ready)

    def test_look_alike_selection_actions_are_mapped(self):
        grid={'state_type':'card_select','card_select':{'preview_showing':False}}
        self.assertEqual(b.validate(grid,'combat_select_card',{'card_index':1}),{'action':'select_card','index':1})
        self.assertEqual(b.validate({'state_type':'hand_select'},'select_card',{'index':2}),{'action':'combat_select_card','card_index':2})
        self.assertNotIn('play_card',b.allowed_actions(grid))
        with self.assertRaises(ValueError): b.validate({'state_type':'map'},'combat_select_card',{'card_index':1})

if __name__=='__main__': unittest.main()
