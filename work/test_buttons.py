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
        with patch.object(b.api,'request',side_effect=responses) as api, patch.object(c,'decide',return_value=decision) as decide, patch.object(c.stop_event,'wait',return_value=False):
            c.run(True,2)
        self.assertEqual(decide.call_count,1)
        posts=[call.args[1] for call in api.call_args_list if len(call.args)==2]
        self.assertEqual(posts,[{'action':'select_card','index':0},{'action':'confirm_selection'}])
        with self.assertRaises(ValueError):
            b.validate(chosen,'select_card',{'index':1})

if __name__=='__main__': unittest.main()
