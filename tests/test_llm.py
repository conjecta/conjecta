from unittest.mock import patch

from math_agent.llm.deepseek import DeepSeekBackend
from math_agent.llm.utils import _estimate_tokens


class TestTokenEstimation:
    def test_cjk_fallback_is_conservative(self):
        # Chinese characters should not be estimated at one token per four chars.
        text = "这是一个中文数学问题的示例文本。" * 10
        estimate = _estimate_tokens(text, model="unknown-model")
        assert estimate >= max(1, len(text) // 2)

    def test_known_model_uses_tiktoken(self):
        estimate = _estimate_tokens("hello world", model="gpt-4")
        assert estimate > 0


class TestDeepSeekBackend:
    @patch("openai.AsyncOpenAI")
    def test_timeout_seconds_passed_to_client(self, mock_async_openai):
        backend = DeepSeekBackend(
            model="deepseek-chat",
            api_key="test-key",
            timeout_seconds=42.0,
        )
        _ = backend.client
        mock_async_openai.assert_called_once()
        kwargs = mock_async_openai.call_args.kwargs
        assert kwargs["timeout"] == 42.0
