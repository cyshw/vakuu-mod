import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import spire_models as m

class Tests(unittest.TestCase):
    def test_selection_roundtrip_and_rejection(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, patch.object(m, 'PREFS', Path(directory)/'settings.json'):
            expected={'model_key':'claude:sonnet','effort':'medium'}
            self.assertEqual(m.save_settings(expected), expected)
            self.assertEqual(m.settings(), expected)
            with self.assertRaises(ValueError): m.save_settings({'model_key':'arbitrary:command'})
            self.assertEqual(m.settings(), expected)

    def test_stale_settings_file_falls_back_to_default(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, patch.object(m, 'PREFS', Path(directory)/'settings.json'):
            for content in ('{"model_key":"codex:removed-model","effort":""}', '[]', 'not json'):
                m.PREFS.write_text(content, encoding='utf-8')
                self.assertEqual(m.settings(), m.DEFAULT_SETTINGS)

    def test_claude_has_no_tools_or_mcp(self):
        with patch.object(m,'find_cli',return_value='claude.exe'):
            args,provider=m.build_command({'model_key':'claude:opus','effort':'high'},Path('run'),{'type':'object'})
        self.assertEqual(provider,'claude')
        self.assertEqual(args[args.index('--tools')+1], '')
        self.assertEqual(json.loads(args[args.index('--mcp-config')+1]), {'mcpServers':{}})
        self.assertIn('--strict-mcp-config',args)
        self.assertIn('--no-session-persistence',args)
        self.assertEqual(args[args.index('--model')+1],'claude-opus-5-5')

    def test_codex_model_and_effort(self):
        with patch.object(m,'find_cli',return_value='codex.exe'):
            args,provider=m.build_command({'model_key':'codex:gpt-6-luna','effort':'low'},Path('run'),{})
        self.assertEqual(provider,'codex')
        self.assertEqual(args[args.index('--model')+1],'gpt-6-luna')
        self.assertEqual(args[args.index('-c')+1],'model_reasoning_effort="low"')
        self.assertEqual(args[-1],'-')

    def test_claude_structured_response_and_errors(self):
        decision={'advice':'選牌','stop':False,'action':'none','parameters_json':'{}'}
        self.assertEqual(m.parse_answer('claude',json.dumps({'structured_output':decision}),None),decision)
        with self.assertRaises(RuntimeError): m.parse_answer('claude','{"is_error":true,"result":"login required"}',None)
        with self.assertRaises(ValueError): m.parse_answer('claude','{"structured_output":{"action":"none"}}',None)
