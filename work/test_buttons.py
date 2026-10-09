import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
path=Path(__file__).resolve().parent.parent/'outputs'/'spire-buttons.py'
spec=importlib.util.spec_from_file_location('buttons',path)
b=importlib.util.module_from_spec(spec); spec.loader.exec_module(b)

class Tests(unittest.TestCase):
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

if __name__=='__main__': unittest.main()
