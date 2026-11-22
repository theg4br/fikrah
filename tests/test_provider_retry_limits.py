"""The worker owns retries; SDK calls remain single-attempt operations."""

from unittest.mock import Mock

import pytest

from app.llm.contracts import NoteAnalysis
from app.llm.providers.deepseek import DeepSeekTextGenerator
from app.llm.providers.gemini import GeminiTextGenerator
from app.llm.providers.openrouter import OpenRouterTextGenerator
from app.rag.embeddings import GeminiEmbeddingModel


@pytest.mark.parametrize("adapter", [DeepSeekTextGenerator, OpenRouterTextGenerator])
def test_openai_compatible_clients_disable_retries_for_every_operation(monkeypatch, adapter):
    constructor = Mock()
    monkeypatch.setattr("app.llm.providers.openai_compatible.OpenAI", constructor)
    generator = adapter("test-key", "test-model")
    options = constructor.call_args.kwargs
    assert options["max_retries"] == 0
    assert options["timeout"] == 90
    failure = TimeoutError("controlled failure")
    create = constructor.return_value.chat.completions.create
    create.side_effect = failure
    with pytest.raises(TimeoutError) as caught:
        generator.generate_structured(
            system_instruction="policy", user_content="input", response_model=NoteAnalysis
        )
    assert caught.value is failure
    create.assert_called_once()


@pytest.mark.parametrize(
    ("adapter", "args"),
    [(GeminiTextGenerator, ("test-key", "model")),
     (GeminiEmbeddingModel, ("test-key", "model", 768))],
)
def test_gemini_clients_explicitly_use_one_attempt(monkeypatch, adapter, args):
    constructor = Mock()
    monkeypatch.setattr("google.genai.Client", constructor)
    adapter(*args)
    options = constructor.call_args.kwargs["http_options"]
    assert options.retry_options.attempts == 1
    assert options.timeout == 90_000
