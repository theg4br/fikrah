"""Adapters clamp waits and close a response when its budget has expired."""

from contextvars import copy_context
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.execution import ExecutionDeadlineExceeded, execution_budget
from app.llm.contracts import ChatAnswer
from app.llm.providers.deepseek import DeepSeekTextGenerator
from app.llm.providers.gemini import GeminiTextGenerator
from app.llm.providers.openrouter import OpenRouterTextGenerator
from app.rag.embeddings import GeminiEmbeddingModel
from app.rag.vector_store import QdrantNoteVectorStore


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter,args,sdk",
    [
        (DeepSeekTextGenerator, ("test-key", "model"), "openai"),
        (OpenRouterTextGenerator, ("test-key", "model"), "openai"),
        (GeminiTextGenerator, ("test-key", "model"), "gemini"),
        (GeminiEmbeddingModel, ("test-key", "model", 768), "gemini"),
    ],
)
async def test_sdk_timeout_is_clamped_to_remaining_budget(
    monkeypatch, adapter, args, sdk
):
    client = Mock()
    constructor = Mock(return_value=client)
    monkeypatch.setattr(
        "app.llm.providers.openai_compatible.OpenAI"
        if sdk == "openai"
        else "google.genai.Client",
        constructor,
    )
    async with execution_budget(5):
        instance = adapter(*args)
        context = copy_context()
        if sdk == "openai":
            assert 0 < constructor.call_args.kwargs["timeout"] <= 5
            assert constructor.call_args.kwargs["max_retries"] == 0
        else:
            options = constructor.call_args.kwargs["http_options"]
            assert 0 < options.timeout <= 5000
            assert options.retry_options.attempts == 1
    try:
        if adapter is GeminiEmbeddingModel:
            operation = lambda: instance.embed_query("not sent")
        else:
            operation = lambda: instance.generate_structured(
                system_instruction="policy",
                user_content="not sent",
                response_model=ChatAnswer,
            )
        with pytest.raises(ExecutionDeadlineExceeded):
            context.run(operation)
        client.chat.completions.create.assert_not_called()
        client.models.generate_content.assert_not_called()
        client.models.embed_content.assert_not_called()
    finally:
        instance.close()
    client.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk", ["openai", "gemini"])
async def test_stream_closes_without_reading_more_chunks_after_scope_finishes(
    monkeypatch, sdk
):
    client = Mock()
    closed = []
    reads = []

    def chunks():
        try:
            if sdk == "openai":
                reads.append("one")
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="one"), finish_reason=None
                        )
                    ]
                )
                reads.append("two")
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="two"), finish_reason=None
                        )
                    ]
                )
            else:
                reads.append("one")
                yield SimpleNamespace(text="one", candidates=[])
                reads.append("two")
                yield SimpleNamespace(text="two", candidates=[])
        finally:
            closed.append(True)

    upstream = chunks()
    if sdk == "openai":
        stream = Mock()
        stream.__enter__ = Mock(return_value=upstream)
        stream.__exit__ = Mock(side_effect=lambda *_: upstream.close())
        client.with_options.return_value = client
        client.chat.completions.create.return_value = stream
        monkeypatch.setattr(
            "app.llm.providers.openai_compatible.OpenAI", Mock(return_value=client)
        )
        adapter = DeepSeekTextGenerator
    else:
        client.models.generate_content_stream.return_value = upstream
        monkeypatch.setattr("google.genai.Client", Mock(return_value=client))
        adapter = GeminiTextGenerator
    async with execution_budget(5):
        instance = adapter("test-key", "model")
        output = instance.stream_structured(
            system_instruction="policy", user_content="input", response_model=ChatAnswer
        )
        assert next(output) == "one"
        # Capture the budget's context, as a real executor thread does.
        context = copy_context()
    try:
        with pytest.raises(ExecutionDeadlineExceeded):
            context.run(next, output)
        assert closed == [True]
        assert reads == ["one"]
    finally:
        output.close()
        instance.close()


@pytest.mark.asyncio
async def test_qdrant_clamps_timeout_and_disables_per_call_background_probe(
    monkeypatch,
):
    client = Mock()
    constructor = Mock(return_value=client)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test")
    monkeypatch.setattr("app.rag.vector_store.QdrantClient", constructor)
    async with execution_budget(5):
        store = QdrantNoteVectorStore()
        assert store._client() is client
    assert 0 < constructor.call_args.kwargs["timeout"] <= 5
    assert constructor.call_args.kwargs["check_compatibility"] is False
