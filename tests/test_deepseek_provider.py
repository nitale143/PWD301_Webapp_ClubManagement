"""DeepSeek / OpenAI-compatible provider: chọn đúng provider, gọi đúng endpoint, đọc đúng kết quả."""
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from app import create_app, db
from app.models import AIUsageLog
from app.services.assistant import (DeepSeekProvider, RulesProvider,
                                     TemporaryAIError, get_provider)
from app.services.common import DomainError


def _reply(text="OK", usage=None):
    payload = {"choices": [{"message": {"role": "assistant", "content": text}}],
               "usage": usage or {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}
    return BytesIO(json.dumps(payload).encode("utf-8"))


class DeepSeekProviderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "deepseek-test-key",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.directory.name) / 'ds.db'}",
            "SCHEDULER_ENABLED": False, "AI_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test-key", "DEEPSEEK_MODEL": "deepseek-chat",
            "DEEPSEEK_BASE_URL": "https://modelapi.vn/v1"})
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.directory.cleanup()

    def test_get_provider_selects_deepseek_when_key_present(self):
        self.assertIsInstance(get_provider(), DeepSeekProvider)

    def test_get_provider_falls_back_to_rules_without_key(self):
        self.app.config["DEEPSEEK_API_KEY"] = ""
        self.assertIsInstance(get_provider(), RulesProvider)

    def test_complete_posts_to_chat_completions_and_reads_content(self):
        captured = {}

        def fake_urlopen(request, timeout=20):
            captured["url"] = request.full_url
            captured["auth"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _reply("Xin chào")

        with patch("app.services.assistant.urlopen", side_effect=fake_urlopen):
            result = DeepSeekProvider().complete("system text", "user text", purpose="chat")

        self.assertEqual(result, "Xin chào")
        self.assertEqual(captured["url"], "https://modelapi.vn/v1/chat/completions")
        self.assertEqual(captured["auth"], "Bearer sk-test-key")
        self.assertEqual(captured["body"]["model"], "deepseek-chat")
        self.assertEqual([m["role"] for m in captured["body"]["messages"]], ["system", "user"])
        self.assertNotIn("response_format", captured["body"])
        logged = db.session.query(AIUsageLog).one()
        self.assertEqual(logged.provider, "deepseek")
        self.assertEqual(logged.total_tokens, 5)

    def test_schema_request_asks_for_json_object(self):
        captured = {}

        def fake_urlopen(request, timeout=20):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _reply("{}")

        with patch("app.services.assistant.urlopen", side_effect=fake_urlopen):
            DeepSeekProvider().complete("s", "p", response_schema={"type": "object"})
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})

    def test_bad_key_raises_domain_error(self):
        error = HTTPError("u", 401, "unauthorized", {}, None)
        with patch("app.services.assistant.urlopen", side_effect=error):
            with self.assertRaises(DomainError) as caught:
                DeepSeekProvider().complete("s", "p")
        self.assertEqual(caught.exception.status, 503)

    def test_transient_network_error_retries_then_gives_up(self):
        with patch("app.services.assistant.urlopen", side_effect=URLError("down")) as urlopen, \
                patch("app.services.assistant.sleep"):
            with self.assertRaises(TemporaryAIError):
                DeepSeekProvider().complete("s", "p")
        self.assertEqual(urlopen.call_count, 2)

    def test_retry_succeeds_on_second_attempt(self):
        attempts = iter([URLError("down"), _reply("recovered")])

        def flaky(request, timeout=20):
            item = next(attempts)
            if isinstance(item, Exception):
                raise item
            return item

        with patch("app.services.assistant.urlopen", side_effect=flaky), \
                patch("app.services.assistant.sleep"):
            self.assertEqual(DeepSeekProvider().complete("s", "p"), "recovered")


if __name__ == "__main__":
    unittest.main()
