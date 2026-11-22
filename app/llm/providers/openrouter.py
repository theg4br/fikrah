"""OpenRouter text-generation adapter."""

from app.llm.providers.openai_compatible import (
    OpenAICompatibleStructuredTextGenerator,
)


class OpenRouterTextGenerator(OpenAICompatibleStructuredTextGenerator):
    """Route chat completions through OpenRouter's OpenAI-compatible API."""

    def __init__(
        self, api_key: str, model: str, *, chat_reasoning_enabled: bool = False
    ) -> None:
        super().__init__(api_key, "https://openrouter.ai/api/v1", model, "OpenRouter")
        self._chat_reasoning_enabled = chat_reasoning_enabled

    def _stream_request_options(self) -> dict:
        return {"extra_body": {"reasoning": {"enabled": self._chat_reasoning_enabled}}}
