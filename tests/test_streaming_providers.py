"""Provider adapters expose chunks and close streams without chat policy."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.llm.contracts import ChatAnswer
from app.llm.providers.deepseek import DeepSeekTextGenerator
from app.llm.providers.gemini import GeminiTextGenerator
from app.llm.providers.openrouter import OpenRouterTextGenerator


@pytest.mark.parametrize("adapter", [DeepSeekTextGenerator, OpenRouterTextGenerator])
@pytest.mark.parametrize("reason", ["stop", "length"])
def test_openai_compatible_stream_closes_and_disables_automatic_retries(
    monkeypatch, adapter, reason
):
    chunks = [
        SimpleNamespace(choices=[]),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="one"), finish_reason=None
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="two"), finish_reason=reason
                )
            ]
        ),
    ]
    stream = Mock()
    stream.__enter__ = Mock(return_value=iter(chunks))
    stream.__exit__ = Mock(return_value=False)
    client = Mock()
    client.with_options.return_value = client
    client.chat.completions.create.return_value = stream
    monkeypatch.setattr(
        "app.llm.providers.openai_compatible.OpenAI", Mock(return_value=client)
    )
    generator = adapter("key", "model")
    output = generator.stream_structured(
        system_instruction="policy", user_content="input", response_model=ChatAnswer
    )
    assert next(output) == "one"
    if reason == "stop":
        assert list(output) == ["two"]
    else:
        with pytest.raises(ValueError, match="did not finish"):
            list(output)
    stream.__exit__.assert_called_once()
    client.with_options.assert_called_once_with(max_retries=0)
    assert client.chat.completions.create.call_args.kwargs["stream"] is True
    kwargs = client.chat.completions.create.call_args.kwargs
    if adapter is OpenRouterTextGenerator:
        assert kwargs["extra_body"] == {"reasoning": {"enabled": False}}
    else:
        assert "extra_body" not in kwargs


@pytest.mark.parametrize("reason", ["STOP", "MAX_TOKENS"])
def test_gemini_stream_closes_and_rejects_incomplete_output(monkeypatch, reason):
    closed = []

    def chunks():
        try:
            yield SimpleNamespace(text="one", candidates=[])
            yield SimpleNamespace(
                text="two", candidates=[SimpleNamespace(finish_reason=reason)]
            )
        finally:
            closed.append(True)

    client = Mock()
    client.models.generate_content_stream.return_value = chunks()
    monkeypatch.setattr(
        "app.llm.providers.gemini.genai.Client", Mock(return_value=client)
    )
    generator = GeminiTextGenerator("key", "model")
    output = generator.stream_structured(
        system_instruction="policy", user_content="input", response_model=ChatAnswer
    )
    assert next(output) == "one"
    if reason == "STOP":
        assert list(output) == ["two"]
    else:
        with pytest.raises(ValueError, match="did not finish"):
            list(output)
    assert closed == [True]
