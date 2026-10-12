import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'vakuu'))
import vakuu_engine as b
import vakuu_facts as facts
import vakuu_plans as plans
import vakuu_router as router
import vakuu_rules as rules
# Route projections use built-in damage defaults here, never live Spire Codex data.
facts.spire_codex.room_damage = lambda *args, **kwargs: None

class Tests(unittest.TestCase):
    def test_stop_triggered_by_state_read_does_not_ask_reward_model(self):
        c=b.Controller(lambda message: None)
        def read(path, body=None):
            self.assertIsNone(body)
            c.stop_event.set()  # one-fight tool observes combat ending in this GET.
            return {'state_type':'rewards', 'player':{'hp':30}}
        with patch.object(b.api,'request',side_effect=read) as api, patch.object(c,'decide_routed') as decide:
            c.run(True,10)
        decide.assert_not_called()
        self.assertEqual(api.call_count,1)

    def test_optional_null_target_is_omitted(self):
        params={'card_index':1,'target':None}
        self.assertEqual(rules.validate({'state_type':'monster'},'play_card',params),
                         {'action':'play_card','card_index':1})
        self.assertIn('target',params)  # Validation does not mutate model output.
        self.assertEqual(rules.validate({'state_type':'monster'},'play_card',{'card_index':1,'target':''}),
                         {'action':'play_card','card_index':1})
        with self.assertRaises(ValueError):
            rules.validate({'state_type':'monster'},'play_card',{'card_index':1,'target':0})

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
            with self.assertRaises(ValueError): rules.validate({'state_type':'event'},action,params)

    def test_valid_action_uses_actual_index(self):
        self.assertEqual(rules.validate({'state_type':'card_reward'},'select_card_reward',{'card_index':9}),{'action':'select_card_reward','card_index':9})

    def test_stop_after_model_prevents_post(self):
        c=b.Controller(lambda message: None)
        def decide(state):
            c.stop()
            return {'advice':'建議','stop':False,'action':'choose_event_option','parameters_json':'{"index":0}'}
        with patch.object(b.api,'request',return_value={'state_type':'event','event':{'options':[{'index':0},{'index':1}]}}) as api, patch.object(c,'decide',side_effect=decide):
            c.run(True,1)
        self.assertTrue(all(len(call.args)==1 for call in api.call_args_list))

    def test_scoped_takeovers_stop_at_the_turn_screen_or_fight_end(self):
        turn1=self.combat(['打擊'],round_=1); turn2=self.combat(['打擊'],round_=2)
        rewards={'state_type':'rewards','rewards':{'items':[{'index':0,'type':'gold'}],'can_proceed':True}}
        self.assertTrue(b.same_screen(turn1,self.combat([],round_=1)))
        self.assertFalse(b.same_screen(turn1,turn2))  # "This turn" ends when the next turn starts.
        shop={'state_type':'shop'}
        self.assertTrue(b.same_screen(shop,{'state_type':'card_select'}))  # Removal is part of the shop visit.
        self.assertFalse(b.same_screen(shop,{'state_type':'map'}))
        self.assertEqual(b.screen_label({'state_type':'map'}),'幫我畫路線')
        end={'advice':'','stop':False,'action':'end_turn','parameters_json':'{}','next_cards':[]}
        def play(scope,states):
            c=b.Controller(lambda message: None)
            reads=iter(states)
            def request(*args): return {'status':'ok'} if len(args)==2 else next(reads)
            with patch.object(b.api,'request',side_effect=request) as api, patch.object(c,'decide_routed',return_value=end), \
                 patch.object(b,'search_turn',return_value={'decision':None,'reason':'x'}), \
                 patch.object(c,'wait_for_change'), patch.object(c.stop_event,'wait',return_value=False):
                c.run(True,scope=scope)
            return [call.args[1] for call in api.call_args_list if len(call.args)==2]
        # This turn: one end_turn, then the next turn's state stops it.
        self.assertEqual(play('screen',[turn1,turn1,turn2,turn2]),[{'action':'end_turn'}])
        # This fight: plays both turns, stops at the rewards without claiming them.
        self.assertEqual(play('fight',[turn1,turn1,turn2,turn2,rewards,rewards]),[{'action':'end_turn'},{'action':'end_turn'}])
        self.assertEqual(play('fight',[rewards]),[])  # Not in a fight: nothing to do.

    def test_elite_and_boss_turns_go_to_the_strong_model_only_when_they_matter(self):
        quiet=self.combat(['打擊'],round_=3,hp=60); quiet['state_type']='elite'; quiet['player']['max_hp']=80
        quiet['battle']['enemies'][0]['intents']=[{'type':'Attack','label':'5'}]
        self.assertFalse(router.hard_screen(quiet))  # 5 of 80 HP at stake: Haiku plays it.
        opening={**quiet,'battle':{**quiet['battle'],'round':1}}
        self.assertTrue(router.hard_screen(opening))  # The first turn sets the plan.
        heavy={**quiet,'battle':{**quiet['battle'],'enemies':[{**quiet['battle']['enemies'][0],'intents':[{'type':'Attack','label':'20'}]}]}}
        self.assertTrue(router.hard_screen(heavy))  # 20 unblocked is a fifth of max HP.

    def test_a_malformed_model_answer_is_asked_again_once(self):
        state=self.combat(['防禦','打擊'])
        broken={'advice':'先打防禦','stop':False,'action':'play_card','parameters_json':'{}','next_cards':[]}
        fixed={**broken,'parameters_json':'{"card_index":0}'}
        c=b.Controller(lambda message: None)
        with patch.object(b.api,'request',side_effect=lambda *args: {'status':'ok'} if len(args)==2 else state) as api, \
             patch.object(c,'decide_routed',side_effect=[broken,fixed]) as model, \
             patch.object(b,'search_turn',return_value={'decision':None,'reason':'x'}), \
             patch.object(c,'wait_for_change'), patch.object(c.stop_event,'wait',return_value=False):
            c.run(True,1)
        self.assertEqual(model.call_count,2)
        self.assertIn('previous_answer_error',model.call_args_list[1].args[0])
        self.assertEqual([call.args[1] for call in api.call_args_list if len(call.args)==2],[{'action':'play_card','card_index':0}])

    def test_coop_waits_for_teammates_after_our_end_turn_or_vote(self):
        fight=self.combat(['打擊']); fight['game_mode']='multiplayer'
        fight['players']=[{'is_local':True,'is_ready_to_end_turn':True},{'is_local':False,'is_ready_to_end_turn':False}]
        self.assertTrue(rules.waiting_for_teammates(fight))
        fight['battle']['all_players_ready']=True
        self.assertFalse(rules.waiting_for_teammates(fight))
        vote={'state_type':'map','game_mode':'multiplayer','map':{'votes':[{'is_local':True,'voted':True},{'is_local':False,'voted':False}],'all_voted':False}}
        self.assertTrue(rules.waiting_for_teammates(vote))
        vote['map']['votes'][0]['voted']=False
        self.assertFalse(rules.waiting_for_teammates(vote))  # Our turn to vote.
        self.assertFalse(rules.waiting_for_teammates({**fight,'game_mode':None}))

    def test_coop_runs_use_the_multiplayer_endpoint(self):
        import io, urllib.error
        api=b.api
        calls=[]
        def urlopen(req, timeout=0):
            calls.append((req.full_url, req.data))
            if '/singleplayer' in req.full_url: raise urllib.error.HTTPError(req.full_url,409,'Conflict',{},io.BytesIO(b'{}'))
            return io.BytesIO(b'{"status":"ok"}')
        with patch.object(api.urllib.request,'urlopen',side_effect=urlopen):
            self.assertEqual(api.request('/api/v1/singleplayer',{'action':'end_turn'}),{'status':'ok'})
        self.assertTrue(calls[-1][0].endswith('/api/v1/multiplayer') and calls[-1][1])

    def test_enchant_preview_with_confirm_disabled_is_waited_out(self):
        applying={'state_type':'card_select','card_select':{'screen_type':'NDeckEnchantSelectScreen','preview_showing':True,
                  'can_confirm':False,'selected_count':1,'min_select':1,'max_select':1}}
        self.assertTrue(rules.transitional(applying))
        self.assertFalse(rules.transitional({**applying,'card_select':{**applying['card_select'],'can_confirm':True}}))

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
            rules.validate(chosen,'select_card',{'index':1})

    def test_prune_keeps_recent_runs_and_settings(self):
        import os, tempfile
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            runtime=Path(directory)/'button-runtime'; runtime.mkdir()
            (runtime/'model-settings.json').write_text('{}')
            for i in range(5):
                run=runtime/f'0000000{i}-0000-0000-0000-000000000000'; run.mkdir(); os.utime(run,(i,i))
            with patch.object(b,'RUNTIME',runtime): b.prune_runs(keep=2)
            self.assertEqual(sorted(p.name[7] for p in runtime.iterdir() if p.is_dir()),['3','4'])
            self.assertTrue((runtime/'model-settings.json').exists())

    def test_auto_decisions_for_single_choice_screens(self):
        act=lambda state: (lambda d: d and (d['action'], json.loads(d['parameters_json'])))(plans.auto_decision(state))
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
            action,params=act(state); rules.validate(state,action,params)

    def test_card_rewards_open_once_each(self):
        state=lambda n: {'state_type':'rewards','run':{'floor':3},'rewards':{'can_proceed':True,
                         'items':[{'index':i,'type':'card'} for i in range(n)]}}
        opened=set()
        self.assertEqual(plans.auto_decision(state(2),opened)['action'],'claim_reward')
        self.assertEqual(plans.auto_decision(state(1),opened)['action'],'claim_reward')  # First was taken.
        self.assertEqual(plans.auto_decision(state(1),opened)['action'],'proceed')  # Skipped: leave instead of reopening it.
        mixed=state(1); mixed['rewards']['items'].append({'index':1,'type':'potion'}); mixed['player']={'potions':[{}],'max_potion_slots':1}
        self.assertIsNone(plans.auto_decision(mixed,opened))  # Something else still needs a decision.
        self.assertIsNone(plans.auto_decision(state(1)))  # Advice mode never auto-opens.

    def test_rest_site_waits_for_proceed_then_leaves(self):
        resting={'state_type':'rest_site','rest_site':{'options':[],'can_proceed':False}}
        ready={'state_type':'rest_site','rest_site':{'options':[],'can_proceed':True}}
        c=b.Controller(lambda message: None)
        with patch.object(b.api,'request',side_effect=[resting,ready]), patch.object(c.stop_event,'wait',return_value=False):
            self.assertEqual(c.wait_until_settled(resting),ready)
        self.assertEqual(plans.auto_decision(ready)['action'],'proceed')
        self.assertIsNone(plans.auto_decision({'state_type':'rest_site','rest_site':{'options':[{'index':0,'is_enabled':True}],'can_proceed':True}}))
        rules.validate(ready,'proceed',{})

    def test_mid_animation_screens_are_waited_out(self):
        for state in ({'state_type':'treasure','treasure':{'message':'Opening chest...','can_proceed':True}},
                      {'state_type':'monster','message':'Combat ended. Waiting for rewards...'},
                      {'state_type':'shop','shop':{'items':[],'can_proceed':False,'error':'not ready'}}):
            self.assertTrue(rules.transitional(state),state)
        for state in ({'state_type':'treasure','treasure':{'relics':[{'index':0}],'can_proceed':True}},
                      {'state_type':'monster','battle':{}},
                      {'state_type':'shop','shop':{'items':[{'index':0}],'can_proceed':True}}):
            self.assertFalse(rules.transitional(state),state)

    def test_treasure_claims_single_relic_then_leaves(self):
        chest={'state_type':'treasure','treasure':{'relics':[{'index':0,'name':'磨刀石'}],'can_proceed':True}}
        decision=plans.auto_decision(chest)
        self.assertEqual(decision['action'],'claim_treasure_relic')
        rules.validate(chest,decision['action'],json.loads(decision['parameters_json']))
        self.assertEqual(plans.auto_decision({'state_type':'treasure','treasure':{'relics':[],'can_proceed':True}})['action'],'proceed')
        self.assertIsNone(plans.auto_decision({'state_type':'treasure','treasure':{'relics':[{'index':0},{'index':1}],'can_proceed':True}}))
        self.assertEqual(plans.auto_decision({'state_type':'treasure','treasure':{'can_proceed':True}})['action'],'proceed')
        self.assertIsNone(plans.auto_decision({'state_type':'treasure','treasure':{'message':'Opening chest...','can_proceed':True}}))

    @staticmethod
    def combat(hand, energy=3, round_=1, enemies=None, block=0, hp=40):
        return {'state_type':'boss','battle':{'round':round_,'turn':'player','is_play_phase':True,
                'enemies':enemies if enemies is not None else [{'entity_id':'BOSS_0','name':'頭目','hp':50,'block':0,
                          'intents':[{'type':'Attack','label':'12×2'},{'type':'Defend','label':''}]}]},
                'player':{'hp':hp,'block':block,'energy':energy,
                          'hand':[{'index':i,'name':n,'cost':'1','can_play':True,'target_type':'AnyEnemy' if '打' in n else 'Self'}
                                  for i,n in enumerate(hand)]}}

    def test_combat_facts_do_the_arithmetic(self):
        result=facts.combat_facts(self.combat(['打擊','防禦'],block=5,hp=19))
        self.assertEqual(result['incoming_attack_damage'],24)
        self.assertEqual(result['block_needed_for_no_damage'],19)
        self.assertTrue(result['lethal_if_no_more_block'])
        self.assertEqual([c['name'] for c in result['playable_cards']],['打擊','防禦'])
        self.assertEqual(facts.incoming_damage({'type':'Buff','label':''}),0)
        self.assertEqual(facts.incoming_damage({'type':'Attack','label':'21'}),21)

    def test_turn_plan_plays_without_asking_again(self):
        first=self.combat(['防禦','打擊','打擊+'])
        decision={'advice':'先防後攻','stop':False,'action':'play_card','parameters_json':'{"card_index":0}',
                  'next_cards':[{'hand_index':2,'target':'BOSS_0'},{'hand_index':1,'target':'BOSS_0'},{'hand_index':rules.END_TURN,'target':''}]}
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

    def test_support_potion_allowed_in_a_dragging_ordinary_fight(self):
        for rnd,expected in ((2,False),(4,True)):
            state=self.combat(['打擊'],hp=50)
            state['state_type']='monster'; state['battle']['round']=rnd; state['player']['max_hp']=80
            state['player']['potions']=[{'slot':0,'name':'易伤药水','description':'给予3层易伤。','can_use_in_combat':True,'target_type':'AnyEnemy'}]
            result=facts.combat_facts(state)
            self.assertEqual(result['usable_potions'][0]['worth_it_in_this_normal_fight'],expected)

    def test_tempo_facts_flag_wasted_block_and_room_to_attack(self):
        state=self.combat(['打擊'],hp=76)
        state['player']['max_hp']=76; state['player']['block']=36
        state['battle']['enemies'][0]['intents']=[{'type':'Attack','label':'28'}]
        result=facts.combat_facts(state)
        self.assertEqual(result['block_surplus'],8)
        self.assertIn('can_afford_to_attack',result)
        state['player']['block']=0; state['player']['hp']=30
        result=facts.combat_facts(state)
        self.assertNotIn('block_surplus',result); self.assertNotIn('can_afford_to_attack',result)

    def test_potions_are_counted_and_planned(self):
        state=self.combat(['打擊','防禦'],hp=20)
        state['player']['potions']=[{'slot':0,'name':'铁皮药水','description':'获得12点格挡。','can_use_in_combat':True,'target_type':'Self'},
                                    {'slot':1,'name':'火焰药水','description':'造成20点伤害。','can_use_in_combat':True,'target_type':'AnyEnemy'}]
        state['player']['max_potion_slots']=2
        state['battle']['enemies'][0]['hp']=18
        result=facts.combat_facts(state)
        self.assertEqual([(p['slot'],p.get('block'),p.get('damage')) for p in result['usable_potions']],[(0,12,None),(1,None,20)])
        self.assertTrue(result['potion_block_covers_lethal'])  # 24 incoming on 20 HP; 12 block leaves 8 HP.
        self.assertTrue(result['potion_kills_an_enemy'])
        self.assertTrue(result['potion_belt_full'])
        self.assertEqual(facts.combat_facts(self.combat(['打擊'])).get('usable_potions'),None)
        decision={'advice':'','stop':False,'action':'play_card','parameters_json':'{"card_index":1}',
                  'next_cards':[{'hand_index':-1,'target':'BOSS_0','potion_slot':1},{'hand_index':0,'target':'BOSS_0','potion_slot':-1}]}
        plan=plans.start_plan(state,decision)
        after=self.combat(['打擊'],energy=2,hp=20); after['player']['potions']=state['player']['potions']; after['battle']['enemies'][0]['hp']=18
        move=plans.plan_decision(plan,after)
        self.assertEqual((move['action'],json.loads(move['parameters_json'])),('use_potion',{'slot':1,'target':'BOSS_0'}))
        after['player']['potions']=after['player']['potions'][:1]
        move=plans.plan_decision(plan,after)
        self.assertEqual((move['action'],json.loads(move['parameters_json'])),('play_card',{'card_index':0,'target':'BOSS_0'}))
        gone=plans.start_plan(state,decision); gone['queue'][0]['name']='别的药水'
        self.assertIsNone(plans.plan_decision(gone,after))  # The planned potion is no longer in that slot.

    def test_frost_orbs_count_as_block(self):
        state=self.combat(['打擊'],hp=20)
        state['player']['orbs']=[{'id':'FROST_ORB','passive_val':2},{'id':'FROST_ORB','passive_val':2},{'id':'LIGHTNING_ORB','passive_val':3}]
        result=facts.combat_facts(state)
        self.assertEqual(result['end_of_turn_orbs'],{'block':4,'damage_to_random_enemy':3})
        self.assertEqual(result['block_needed_for_no_damage'],20)  # 24 incoming minus 4 Frost block.
        self.assertTrue(result['lethal_if_no_more_block'])

    def test_last_elite_before_boss_is_flagged_when_the_boss_leaves_too_little(self):
        rooms=[{'type':'Elite','row':13},{'type':'RestSite','row':14},{'type':'Boss','row':15}]
        damage={**facts.DEFAULT_DAMAGE,'elite':35,'boss':54}
        projected=facts.project_route(rooms,59,75,damage)
        self.assertEqual(projected['boss_average_damage'],54)
        self.assertEqual(projected['hp_after_boss_if_average'],projected['hp_before_boss']-54)
        self.assertEqual(facts.deck_profile([{'id':'DEFEND_D','description':'获得5点格挡。'}],2)['short_of_block'],True)

    def test_rest_is_taken_without_the_model_when_the_projection_says_so(self):
        rest={'state_type':'rest_site','run':{'act':1,'floor':29},'player':{'hp':43,'max_hp':75,'character_id':'DEFECT'},
              'rest_site':{'options':[{'index':0,'id':'HEAL','name':'休息','is_enabled':True},{'index':1,'id':'SMITH','is_enabled':True}]}}
        route={'path':[(4,12),(5,13),(6,14)],'types':{(4,12):'Elite',(5,13):'Unknown',(6,14):'RestSite'}}
        move=plans.rest_decision(route,rest)
        self.assertEqual((move['action'],json.loads(move['parameters_json'])),('choose_rest_option',{'index':0}))
        calm={'path':[(4,12),(5,13)],'types':{(4,12):'Shop',(5,13):'RestSite'}}
        self.assertIsNone(plans.rest_decision(calm,rest))  # Safe ahead: the model weighs upgrading.
        self.assertIsNone(plans.rest_decision(None,rest))

    def test_runic_pyramid_keeps_the_hand_and_setup_potions_are_listed_in_hard_fights(self):
        state=self.combat(['打擊'])
        state['player']['relics']=[{'id':'RUNIC_PYRAMID'}]
        state['player']['potions']=[{'slot':0,'name':'力量药水','description':'获得2点力量。','can_use_in_combat':True},
                                    {'slot':1,'name':'火焰药水','description':'造成20点伤害。','can_use_in_combat':True}]
        result=facts.combat_facts(state)
        self.assertFalse(result['unplayed_cards_are_discarded_at_end_of_turn'])
        self.assertIn('不因回合結束而棄掉', result['hand_end_of_turn_rule'])
        self.assertEqual(result['setup_potions_drink_in_first_turns'],['力量药水'])  # combat() is a boss fight.
        state['state_type']='monster'
        self.assertNotIn('setup_potions_drink_in_first_turns',facts.combat_facts(state))

    def test_pile_contents_are_name_counts(self):
        player={'draw_pile':[{'name':'打擊'},{'name':'打擊'},{'name':'防禦'}],'discard_pile':[],'exhaust_pile':[{'name':'燃燒'}]}
        self.assertEqual(facts.pile_contents(player),{'draw_pile':{'打擊':2,'防禦':1},'exhaust_pile':{'燃燒':1}})

    def test_plan_is_dropped_when_the_turn_surprises_it(self):
        first=self.combat(['快速檢索','打擊'])
        decision={'advice':'抽牌','stop':False,'action':'play_card','parameters_json':'{"card_index":0}',
                  'next_cards':[{'hand_index':1,'target':'BOSS_0'}]}
        self.assertIsNone(plans.plan_decision(plans.start_plan(first,decision),self.combat(['打擊','防禦'])))  # Drew a card.
        self.assertIsNone(plans.plan_decision(plans.start_plan(first,decision),self.combat(['打擊'],round_=2)))  # New turn.
        self.assertIsNone(plans.plan_decision(plans.start_plan(first,decision),self.combat(['打擊'],enemies=[])))  # Target died.
        self.assertIsNone(plans.plan_decision(plans.start_plan(first,decision),self.combat([])))  # Card is gone.
        self.assertIsNotNone(plans.plan_decision(plans.start_plan(first,decision),self.combat(['打擊'])))
        self.assertIsNone(plans.start_plan({'state_type':'map'},decision))

    def test_auto_mode_routes_and_escalates(self):
        easy=self.combat(['打擊'],hp=40); easy['state_type']='monster'; easy['player']['max_hp']=40
        easy['battle']['enemies'][0]['intents']=[]
        hard=self.combat(['打擊'])  # Boss.
        self.assertFalse(router.hard_screen(easy)); self.assertTrue(router.hard_screen(hard))
        self.assertTrue(router.hard_screen({'state_type':'map','map':{'next_options':[{'index':0},{'index':1}]}}))
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
        self.assertIsNone(plans.auto_decision(pick(0)))
        self.assertEqual(plans.auto_decision(pick(1))['action'],'confirm_selection')
        with self.assertRaises(ValueError): rules.validate(pick(1),'select_card',{'index':0})
        self.assertFalse(rules.selection_complete({'state_type':'card_select','card_select':{'selected_count':1,'max_select':3}}))

    def test_waits_until_action_shows_up_and_while_loading(self):
        old={'state_type':'map','map':{'current_position':{'row':2}}}
        moved={'state_type':'unknown'}
        c=b.Controller(lambda message: None)
        with patch.object(b.api,'request',side_effect=[old,old,moved,moved]) as api, patch.object(c.stop_event,'wait',return_value=False):
            c.wait_for_change(old)
        self.assertEqual(api.call_count,4)  # Returns once two reads after the change agree.
        partly={'state_type':'monster','player':{'hand':[],'orb_slots':3}}
        done={'state_type':'monster','player':{'hand':[],'orb_slots':5}}
        with patch.object(b.api,'request',side_effect=[partly,done,done]) as api, patch.object(c.stop_event,'wait',return_value=False):
            c.wait_for_change({'state_type':'monster','player':{'hand':[{'name':'扩容'}],'orb_slots':3}})
        self.assertEqual(api.call_count,3)  # Kept reading until the orb slots landed.
        self.assertTrue(rules.transitional(moved))
        self.assertTrue(rules.transitional({'state_type':'map','map':{'next_options':[]}}))
        self.assertFalse(rules.transitional({'state_type':'map','map':{'next_options':[{'index':0}]}}))
        self.assertTrue(rules.transitional({'state_type':'event','event':{'options':[],'in_dialogue':False}}))
        self.assertFalse(rules.transitional({'state_type':'event','event':{'options':[],'in_dialogue':True}}))

    def test_plan_uses_game_names_and_piles_do_not_abort(self):
        hand=self.combat(['引雷针','防禦'])
        decision={'advice':'','stop':False,'action':'play_card','parameters_json':'{"card_index":1}',
                  'next_cards':[{'hand_index':0,'target':''},{'hand_index':9,'target':''}]}
        plan=plans.start_plan(hand,decision)
        self.assertEqual([e['name'] for e in plan['queue']],['引雷针'])  # Out-of-range index ends the plan.
        before=dict(hand,player=dict(hand['player'],discard_pile=[],discard_pile_count=0))
        after=dict(hand,player=dict(hand['player'],discard_pile=[{'name':'黏液'}],discard_pile_count=1))
        self.assertEqual(rules.fingerprint(before),rules.fingerprint(after))
        self.assertNotEqual(rules.fingerprint(hand),rules.fingerprint(self.combat(['引雷针'])))

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
        self.assertEqual(rules.validate(grid,'combat_select_card',{'card_index':1}),{'action':'select_card','index':1})
        self.assertEqual(rules.validate({'state_type':'hand_select'},'select_card',{'index':2}),{'action':'combat_select_card','card_index':2})
        self.assertNotIn('play_card',rules.allowed_actions(grid))
        with self.assertRaises(ValueError): rules.validate({'state_type':'map'},'combat_select_card',{'card_index':1})
        self.assertEqual(rules.validate({'state_type':'monster'},'play_card',{'hand_index':4}),{'action':'play_card','card_index':4})

    def test_deck_summary_merges_copies(self):
        deck=[{'id':'STRIKE','name':'打擊','is_upgraded':False,'cost':'1','description':'造成6點傷害。'}]*4+[
              {'id':'STRIKE','name':'打擊+','is_upgraded':True,'cost':'1','description':'造成9點傷害。'}]
        summary=facts.deck_summary(deck)
        self.assertEqual([(c['name'],c['count']) for c in summary],[('打擊',4),('打擊+',1)])
        self.assertEqual(summary[0]['description'],'造成6點傷害。')

    def test_shop_list_survives_card_removal(self):
        def shop(gold_ok=True, removal_stocked=True):
            return {'state_type':'shop','shop':{'can_proceed':True,'items':[
                {'index':0,'category':'card','card_name':'混沌','is_stocked':True,'can_afford':gold_ok},
                {'index':13,'category':'card_removal','is_stocked':removal_stocked,'can_afford':True}]}}
        picking={'state_type':'card_select','card_select':{'preview_showing':False,'cards':[{'index':0}]}}
        buy={'advice':'','stop':False,'action':'shop_purchase','parameters_json':'{"index":13}','next_cards':[],
             'next_purchases':[0,rules.LEAVE_SHOP]}
        remove={'advice':'','stop':False,'action':'select_card','parameters_json':'{"index":0}','next_cards':[],'next_purchases':[]}
        after=shop(removal_stocked=False)
        c=b.Controller(lambda message: None)
        responses=[shop(),shop(),{'status':'ok'},picking,picking,{'status':'ok'},after,after,{'status':'ok'},after,after,{'status':'ok'}]
        with patch.object(b.api,'request',side_effect=responses) as api, patch.object(c,'decide_routed',side_effect=[buy,remove]) as decide,              patch.object(c.stop_event,'wait',return_value=False), patch.object(c,'wait_for_change'):
            c.run(True,4)
        self.assertEqual(decide.call_count,2)
        posts=[call.args[1] for call in api.call_args_list if len(call.args)==2]
        self.assertEqual(posts,[{'action':'shop_purchase','index':13},{'action':'select_card','index':0},
                                {'action':'shop_purchase','index':0},{'action':'proceed'}])
        plan=plans.start_plan(shop(),dict(buy,parameters_json='{"index":0}',next_purchases=[13]))
        self.assertIsNone(plans.plan_decision(plan,shop(removal_stocked=False)))  # Sold out: ask again.

    def test_full_potion_belt_rejects_potion_claims_and_repeats_stop_takeover(self):
        rewards={'state_type':'rewards','player':{'potions':[{},{}],'max_potion_slots':2},
                 'rewards':{'items':[{'index':0,'type':'potion'},{'index':1,'type':'gold'}],'can_proceed':True}}
        with self.assertRaises(ValueError): rules.validate(rewards,'claim_reward',{'index':0})
        self.assertEqual(rules.validate(rewards,'claim_reward',{'index':1}),{'action':'claim_reward','index':1})
        self.assertFalse(rules.potion_belt_full({'player':{'potions':[{}]}}))  # Unknown slot count: not full.
        same={'advice':'領','stop':False,'action':'claim_reward','parameters_json':'{"index":1}'}
        c=b.Controller(lambda message: None)
        with patch.object(b.api,'request',side_effect=lambda *args: {'status':'ok'} if len(args)==2 else rewards) as api, \
             patch.object(c,'decide_routed',return_value=same), patch.object(b,'auto_decision',return_value=None), \
             patch.object(c,'wait_for_change'), patch.object(c.stop_event,'wait',return_value=False):
            c.run(True,10)
        posts=[call for call in api.call_args_list if len(call.args)==2]
        self.assertEqual(len(posts),b.REPEAT_LIMIT-1)  # The third identical send is not made.

    def test_late_draw_keeps_the_chosen_play(self):
        before=self.combat(['黏液','打擊'])
        drew=self.combat(['黏液','打擊','集中打擊'])
        body={'action':'play_card','card_index':1,'target':'BOSS_0'}
        self.assertTrue(rules.still_valid(before,drew,body))
        self.assertFalse(rules.still_valid(before,self.combat(['打擊','黏液']),body))  # Hand reordered or played by hand.
        self.assertFalse(rules.still_valid(before,self.combat(['黏液']),body))
        self.assertFalse(rules.still_valid(before,self.combat(['黏液','打擊'],enemies=[]),body))  # Target died.
        potion={'slot':1,'name':'格挡药水'}
        for state in (before,drew): state['player']['potions']=[potion]
        drink={'action':'use_potion','slot':1}
        self.assertTrue(rules.still_valid(before,drew,drink))
        drank=self.combat(['黏液','打擊','集中打擊']); drank['player']['potions']=[]
        self.assertFalse(rules.still_valid(before,drank,drink))  # Drunk by hand meanwhile.
        self.assertFalse(rules.still_valid({'state_type':'map','map':{'a':1}},{'state_type':'map','map':{'a':2}},{'action':'choose_map_node','index':0}))

    def test_death_blow_counts_as_incoming_damage(self):
        # Waterfall Giant: after "dying" it becomes unkillable and hits for 53; this once read as 0 and lost the run.
        dying={'entity_id':'WATERFALL_GIANT_0','name':'瀑布巨兽','hp':999999990,'block':0,
               'intents':[{'type':'DeathBlow','label':'53','title':'濒死一击','description':'它会在被摧毁前攻击你造成53点伤害。'}]}
        result=facts.combat_facts(self.combat(['防禦'],enemies=[dying],hp=17))
        self.assertEqual(result['incoming_attack_damage'],53)
        self.assertTrue(result['lethal_if_no_more_block'])
        self.assertEqual(result['special_intents'][0]['type'],'DeathBlow')
        self.assertEqual(facts.incoming_damage({'type':'Debuff','label':'','description':'这个敌人将要对你施加一个负面效果。'}),0)
        self.assertEqual(facts.combat_facts(self.combat(['防禦']))['special_intents'],[])

    def test_energy_card_is_skipped_when_energy_already_suffices(self):
        def hand(cards, energy=3):
            state=self.combat([n for n,_,_ in cards],energy=energy)
            for c,(name,cost,desc) in zip(state['player']['hand'],cards): c.update(cost=cost,description=desc)
            return state
        SUPER=('超临界态','0','获得4[defect_energy_icon.png]。 消耗。')
        DEF=('防御','1','获得5点格挡。'); ZAP=('电击','1','生成1个闪电充能球。')
        TURBO=('抢占先机','0','在下个回合获得[defect_energy_icon.png][defect_energy_icon.png]。')
        # The recorded turn: 3 energy covers Defend, Defend, Zap, so Supercritical's 4 energy would be wasted.
        wasteful=hand([DEF,DEF,ZAP,SUPER])
        self.assertEqual(facts.spare_energy(wasteful),0)
        self.assertTrue(facts.wastes_energy_card(wasteful,wasteful['player']['hand'][3]))
        self.assertFalse(facts.gives_energy_now({'description':TURBO[2]}))  # Next turn's energy is a different budget.
        sever={'type':'Attack','description':'造成26点伤害。 如果这张牌击杀了敌人，则获得[defect_energy_icon.png][defect_energy_icon.png][defect_energy_icon.png]。'}
        self.assertFalse(facts.gives_energy_now(sever))  # An attack with a kill bonus is never "just energy".
        self.assertFalse(facts.gives_energy_now({'type':'Skill','description':'抽1张牌。 如果手牌为空，则获得2[defect_energy_icon.png]。'}))
        short=hand([DEF,DEF,ZAP,SUPER],energy=1)
        short['player']['hand'][1]['can_play']=False; short['player']['hand'][1]['unplayable_reason']='EnergyCostTooHigh'
        self.assertFalse(facts.wastes_energy_card(short,short['player']['hand'][3]))  # Needed to afford the rest.
        decision={'advice':'','stop':False,'action':'play_card','parameters_json':'{"card_index":3}',
                  'next_cards':[{'hand_index':0,'target':''},{'hand_index':1,'target':''},{'hand_index':2,'target':''},{'hand_index':-1,'target':''}]}
        c=b.Controller(lambda message: None)
        after1=hand([DEF,ZAP,SUPER],energy=2); after2=hand([ZAP,SUPER],energy=1); after3=hand([SUPER],energy=0)
        responses=[wasteful,wasteful,{'status':'ok'},after1,after1,{'status':'ok'},after2,after2,{'status':'ok'},after3,after3,{'status':'ok'}]
        with patch.object(b.api,'request',side_effect=responses) as api, patch.object(c,'decide_routed',return_value=decision),              patch.object(c.stop_event,'wait',return_value=False), patch.object(c,'wait_for_change'):
            c.run(True,4)
        posts=[call.args[1] for call in api.call_args_list if len(call.args)==2]
        self.assertEqual(posts,[{'action':'play_card','card_index':0},{'action':'play_card','card_index':0},
                                {'action':'play_card','card_index':0},{'action':'end_turn'}])  # Supercritical kept.

    def test_brief_and_combat_modes(self):
        fight=self.combat(['打擊'],hp=40); fight['state_type']='monster'; fight['player']['max_hp']=40
        fight['battle']['enemies'][0]['intents']=[]; fight['run']={'act':1,'floor':5}
        good={'advice':'','stop':False,'action':'end_turn','parameters_json':'{}','next_cards':[],'next_purchases':[],'strategy':'先殺左邊'}
        c=b.Controller(lambda message: None)
        prompts=[]
        def fake(state, selection):
            prompts.append((selection['model_key'], c.want_strategy))
            if state.get('state_type') == 'event': return dict(good, action='choose_event_option', parameters_json='{"index":0}')
            return dict(good)
        with patch.object(b.models,'settings',return_value={'model_key':'auto:brief','effort':'low'}), patch.object(c,'decide',side_effect=fake):
            c.decide_routed(fight); c.decide_routed(fight)
        self.assertEqual(prompts,[('claude:sonnet',True),('claude:haiku',False)])  # Plan once, then Haiku follows it.
        self.assertEqual(c.strategy,{'fight':(1,5),'text':'先殺左邊'})
        prompts.clear()
        with patch.object(b.models,'settings',return_value={'model_key':'auto:combat','effort':'low'}), patch.object(c,'decide',side_effect=fake):
            c.decide_routed(fight); c.decide_routed({'state_type':'event','event':{'options':[{'index':0},{'index':1,'is_locked':True}]}})
        self.assertEqual([m for m,_ in prompts],['claude:sonnet','claude:haiku'])

    def test_route_facts_expose_forced_elites(self):
        # Recorded run: resting right away left a single path into an elite two rooms later.
        node=lambda col,row,kind,*kids: {'col':col,'row':row,'type':kind,'children':[list(k) for k in kids]}
        nodes=[node(2,6,'Monster',(2,7)), node(2,7,'RestSite',(2,8)), node(2,8,'Monster',(2,9)), node(2,9,'Monster'),
               node(3,6,'RestSite',(3,7)), node(3,7,'Monster',(4,8)), node(4,8,'Elite',(4,9)), node(4,9,'Monster')]
        state={'state_type':'map','player':{'hp':25,'max_hp':75},'map':{'nodes':nodes,'next_options':[
            {'index':0,'col':2,'row':6,'type':'Monster'},{'index':1,'col':3,'row':6,'type':'RestSite'}]}}
        fight,rest=facts.route_facts(state)['options']
        self.assertEqual(fight['until_next_rest_site'],{'steps':1,'elites':[0,0],'fights':[1,1]})
        self.assertEqual(fight['max_rooms_before_first_elite'],4)
        self.assertEqual(rest['until_next_rest_site']['steps'],0)
        self.assertEqual(rest['max_rooms_before_first_elite'],2)
        self.assertEqual(rest['to_act_end_min_max']['elites'],[1,1])
        self.assertEqual(facts.route_facts(state)['hp_ratio'],0.33)
        routes={r['style']:r for r in facts.route_facts(state)['candidate_routes']}
        self.assertEqual(routes['safest']['first_elite_at_room'],None)
        self.assertEqual([(x['col'],x['row']) for x in routes['safest']['rooms']],[(2,6),(2,7),(2,8),(2,9)])
        elite=routes.get('elite_hunt') or routes['balanced']  # Identical routes are listed once.
        self.assertEqual(elite['first_elite_at_room'],3)
        self.assertEqual(elite['option_index'],1)

    def test_route_is_planned_once_and_followed(self):
        def at(row, options, hp=60, act=1):
            return {'state_type':'map','run':{'act':act},'player':{'hp':hp,'max_hp':75},
                    'map':{'current_position':{'row':row},'next_options':[{'index':i,'col':c,'row':row+1} for i,c in enumerate(options)]}}
        drawn={'action':'choose_map_node','parameters_json':'{"index":0}',
               'next_nodes':[{'col':2,'row':2},{'col':3,'row':3},{'col':3,'row':4}]}
        route=plans.start_route(at(0,[1,4]),drawn)
        self.assertEqual(route['path'],[(2,2),(3,3),(3,4)])
        self.assertEqual(json.loads(plans.route_decision(route,at(1,[0,2]))['parameters_json']),{'index':1})
        # Row 3 had a single path, taken by the forced-move rule; the route skips it and continues.
        self.assertEqual(json.loads(plans.route_decision(route,at(3,[1,3]))['parameters_json']),{'index':1})
        self.assertIsNone(plans.route_decision(plans.start_route(at(0,[1,4]),drawn),at(1,[0,1])))  # Left the route.
        self.assertIsNone(plans.route_decision(plans.start_route(at(0,[1,4]),drawn),at(1,[2],hp=40)))  # Lost 20 of 75 HP.
        self.assertIsNone(plans.route_decision(plans.start_route(at(0,[1,4]),drawn),at(1,[2],act=2)))  # New act.
        self.assertIsNone(plans.start_route({'state_type':'shop'},drawn))
        elite_route=plans.start_route(at(0,[1,4]),drawn); elite_route['types'][(2,2)]='Elite'
        self.assertIsNone(plans.route_decision(elite_route,at(1,[0,2])))  # Always re-asked before an elite.

    def test_guide_rules_rest_balanced_routes_and_pick_threshold(self):
        node=lambda col,row,kind,*kids: {'col':col,'row':row,'type':kind,'children':[list(k) for k in kids]}
        nodes=[node(0,1,'Monster',(0,2)), node(0,2,'Elite',(0,3)), node(0,3,'RestSite',(0,4)), node(0,4,'Monster'),
               node(1,1,'Monster',(1,2)), node(1,2,'Monster',(1,3)), node(1,3,'Unknown',(1,4)), node(1,4,'Monster'),
               node(2,1,'Elite',(2,2)), node(2,2,'Monster',(2,3)), node(2,3,'Monster',(2,4)), node(2,4,'Monster')]
        state={'state_type':'map','player':{'hp':60,'max_hp':75},'map':{'nodes':nodes,'next_options':[
            {'index':i,'col':c,'row':1,'type':'?'} for i,c in enumerate((0,1,2))]}}
        routes={r['style']:r for r in facts.route_facts(state)['candidate_routes']}
        self.assertEqual(routes['balanced']['option_index'],0)  # The elite followed by a rest site.
        self.assertEqual(routes['balanced']['counts']['elites_rest_after'],1)
        self.assertEqual(routes['safest']['option_index'],1)
        rest={'state_type':'rest_site','player':{'hp':20,'max_hp':75},'rest_site':{'options':[{'index':0},{'index':1}]}}
        route={'path':[(0,5),(0,6)],'types':{(0,5):'Elite',(0,6):'Boss'}}
        result=facts.rest_facts(rest,route)
        self.assertEqual({k:result[k] for k in ('hp_ratio','rest_heals','hp_after_rest','planned_next_rooms')},
                         {'hp_ratio':0.27,'rest_heals':22,'hp_after_rest':42,'planned_next_rooms':['Elite','Boss']})
        self.assertTrue(result['rest_recommended'])  # Upgrading leaves an elite to face at 20 HP: projected below 30%.
        self.assertTrue(router.hard_screen(rest))
        reward=lambda top: {'state_type':'card_reward'}
        with patch.object(plans,'advise',return_value={'status':'ok','ranked_cards':[{'index':0,'name':'A','score':55},{'index':1,'name':'B','score':30}]}):
            self.assertIsNone(plans.auto_decision({'state_type':'card_reward'}))  # Clear winner, but only 55.
        with patch.object(plans,'advise',return_value={'status':'ok','deck_direction':{'unknown_description_count':0},
            'ranked_cards':[{'index':0,'name':'A','score':80,'deck_fit':{'effect_known':True,'fills_needs':['damage']}},
                            {'index':1,'name':'B','score':30,'deck_fit':{'effect_known':True}}]}):
            self.assertEqual(plans.auto_decision({'state_type':'card_reward'})['action'],'select_card_reward')

    def test_enemy_notes_match_enemies_in_the_fight(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            notes=Path(directory)/'notes.md'
            notes.write_text('# 筆記\n- CORPSE_SLUG：先打死一隻\n- TOADPOLE: 集火\n不是筆記的行\n',encoding='utf-8')
            with patch.object(facts,'ENEMY_NOTES',notes):
                fight={'battle':{'enemies':[{'entity_id':'CORPSE_SLUG_0'},{'entity_id':'CORPSE_SLUG_1'},{'entity_id':'NIBBIT_0'}]}}
                self.assertEqual(facts.enemy_notes(fight),{'CORPSE_SLUG':'先打死一隻'})
            with patch.object(facts,'ENEMY_NOTES',Path(directory)/'missing.md'):
                self.assertEqual(facts.enemy_notes(fight),{})

    def test_hand_pick_confirms_when_the_prompted_count_is_chosen(self):
        pick=lambda chosen, prompt='选择1张牌来消耗。': {'state_type':'hand_select','hand_select':{
            'prompt':prompt,'can_confirm':True,'cards':[{'index':0,'name':'打击'}],'selected_cards':[{'name':'打击'}]*chosen}}
        self.assertEqual(plans.auto_decision(pick(1))['action'],'combat_confirm_selection')
        self.assertIsNone(plans.auto_decision(pick(0)))
        self.assertIsNone(plans.auto_decision(pick(1,'选择2张牌来消耗。')))
        self.assertIsNone(plans.auto_decision(pick(1,'选择任意张牌。')))  # No count: the model decides.
        self.assertEqual(rules.validate(pick(0),'play_card',{'card_index':0,'target':'SEWER_CLAM_0'}),
                         {'action':'combat_select_card','card_index':0})

    def test_deck_profile_and_hard_shop_event_screens(self):
        deck=[{'id':'STRIKE_DEFECT','type':'Attack','description':'造成6点伤害。'}]*4+[
              {'id':'DEFEND_DEFECT','type':'Skill','description':'获得5点格挡。'}]*4+[
              {'id':'GLACIER','type':'Skill','description':'获得6点格挡。 生成2个冰霜充能球。'},
              {'id':'ASCENDERS_BANE','type':'Curse','description':'不能被打出。'}]
        profile=facts.deck_profile(deck)
        self.assertEqual((profile['attacks'],profile['block_sources'],profile['block_sources_besides_defend'],
                          profile['starter_strikes'],profile['unplayable']),(4,5,1,4,1))
        self.assertTrue(router.hard_screen({'state_type':'shop','shop':{'items':[]}}))
        self.assertTrue(router.hard_screen({'state_type':'event','event':{'options':[{'index':0},{'index':1}]}}))
        self.assertFalse(router.hard_screen({'state_type':'event','event':{'options':[{'index':0}]}}))

    def test_timed_effects_and_keyword_glossary(self):
        player={'status':[{'name':'炸弹','description':'在第3回合结束时，对所有敌人造成40点伤害。'},
                          {'name':'炸弹','description':'在第1回合结束时，对所有敌人造成40点伤害。'},
                          {'name':'集中','description':'提升充能球的效力1点。'}]}
        effects=facts.timed_effects(player)
        self.assertEqual([(e['turns_left'],e['fires_at_end_of_this_turn']) for e in effects],[(3,False),(1,True)])
        data={'hand':[{'name':'A','keywords':[{'name':'消耗','description':'移除'}]},{'name':'B','keywords':[{'name':'消耗','description':'移除'}]}]}
        self.assertEqual(facts.pull_keywords(data),{'消耗':'移除'})
        self.assertEqual(data['hand'][1]['keywords'],['消耗'])

    def test_route_projection_and_replan_when_behind(self):
        rooms=[{'type':'Monster','row':r} for r in (4,5,6,7,8)]+[{'type':'RestSite','row':9},{'type':'Boss','row':10}]
        projected=facts.project_route(rooms,60,75,facts.DEFAULT_DAMAGE)
        self.assertEqual(projected['hp_by_room'][:5],[46,32,18,4,-10])  # Five normal fights of 14 with no rest.
        self.assertEqual(projected['below_25pct_at_room'],3)
        self.assertEqual(projected['lowest_before_first_rest'],-10)
        self.assertEqual(facts.project_route([{'type':'Monster','row':4},{'type':'RestSite','row':5},{'type':'Elite','row':6}],60,75,facts.DEFAULT_DAMAGE)['lowest_before_first_rest'],46)
        rested=facts.project_route([{'type':'Monster','row':4},{'type':'RestSite','row':5}],25,75,facts.DEFAULT_DAMAGE)
        self.assertEqual(rested['hp_by_room'],[11,34])  # Below 40% the rest site heals 22.
        def at(col,row,kind,hp,options):
            return {'state_type':'map','run':{'act':1},'player':{'hp':hp,'max_hp':75},
                    'map':{'current_position':{'col':col,'row':row,'type':kind},'next_options':[{'index':i,'col':c,'row':row+1} for i,c in enumerate(options)],
                           'nodes':[{'col':c,'row':r,'type':'Monster'} for c in range(4) for r in range(1,6)]}}
        drawn={'action':'choose_map_node','parameters_json':'{"index":0}','next_nodes':[{'col':0,'row':2},{'col':0,'row':3}]}
        route=plans.start_route(at(0,0,'Ancient',60,[0,1]),drawn)
        self.assertEqual(route['expected_hp'][(0,1)],52)
        self.assertIsNotNone(plans.route_decision(route,at(0,1,'Monster',50,[0,1])))  # On plan.
        route=plans.start_route(at(0,0,'Ancient',60,[0,1]),drawn)
        self.assertIsNone(plans.route_decision(route,at(0,1,'Monster',40,[0,1])))  # 12 below the 52 projected.
        route=plans.start_route(at(0,0,'Ancient',60,[0,1]),drawn)
        self.assertIsNone(plans.route_decision(route,at(0,1,'RestSite',60,[0,1])))  # Rethink after a rest site.

if __name__=='__main__': unittest.main()
