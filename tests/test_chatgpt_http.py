"""Real HTTP routes; all identities and providers here are synthetic."""
import unittest
from unittest.mock import patch
import test_personal_http as helper
from course_assistant.model_client import ModelClient


class ChatGPTHTTPTests(unittest.TestCase):
    request = helper.PersonalHTTPTests.request
    login = helper.PersonalHTTPTests.login
    setUp = helper.PersonalHTTPTests.setUp
    tearDown = helper.PersonalHTTPTests.tearDown

    def test_connection_requires_session_csrf_and_same_origin(self):
        self.assertEqual(self.request('GET', '/api/chat/chatgpt/status')[0], 401)
        self.login()
        self.assertEqual(self.request('POST', '/api/chat/chatgpt/connect', {}, csrf=False)[0], 403)
        self.assertEqual(self.request('POST', '/api/chat/chatgpt/connect', {}, origin='https://evil.invalid')[0], 403)
        with patch.object(self.app.chatgpt, 'begin', return_value={'authorization_url': 'https://auth.openai.com/api/accounts/authorize?state=synthetic'}):
            code, result, _ = self.request('POST', '/api/chat/chatgpt/connect', {})
            self.assertEqual(code, 200, result)
        code, result, _ = self.request('GET', '/api/chat/chatgpt/status')
        self.assertEqual(code, 200, result)
        self.assertFalse(result['connected'])
        self.assertEqual(self.clients[0].calls, [])

    def test_reasoning_strength_settings_roundtrip_without_inference(self):
        self.login()
        self.app.personal_chat.model = ModelClient({'DEEPSEEK_API_KEY': 'SYNTHETIC'})
        code, catalog, _ = self.request('GET', '/api/chat/models')
        self.assertEqual(code, 200, catalog)
        self.assertEqual(catalog['models'][0]['effort']['supported_levels'], ['low', 'high', 'max'])
        code, result, _ = self.request('POST', '/api/chat/settings', {'model': 'deepseek-flash', 'thinking': True, 'reasoning_effort': 'max'})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['reasoning_effort'], 'max')
        self.assertEqual(self.request('POST', '/api/chat/settings', {'model': 'deepseek-flash', 'thinking': True, 'reasoning_effort': 'medium'})[0], 400)
        self.assertEqual(self.clients[0].calls, [])
