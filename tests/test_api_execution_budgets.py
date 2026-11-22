"""HTTP/SSE budget failures never save late replies or release running capacity."""

import asyncio
from datetime import datetime, timezone
from threading import Event
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlalchemy import select

from app.contracts.search import SearchMatch
from app.core.execution import remaining_time
from app.infrastructure.container import chat_service, search_service
from app.infrastructure.execution import BoundedExecutor
from app.llm.contracts import ChatAnswer
from app.models import ChatMessage
from tests import test_chat_api, test_chat_streaming, test_search_api

chat_api = test_chat_api.chat_api
search_api = test_search_api.search_api


async def event(event):
    assert await asyncio.to_thread(event.wait, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["embedding", "vectors"])
async def test_search_deadline_returns_existing_503_and_retains_running_capacity(
    search_api, monkeypatch, stage
):
    client, _, _, _, project_id, note_id, *_, token = search_api
    started, release, closed = Event(), Event(), Event()
    pool = BoundedExecutor(1, "search-test")
    monkeypatch.setenv("FIKRAH_SEARCH_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setattr(search_service, "_run_blocking", pool.run)

    def block():
        started.set()
        assert release.wait(3)

    class Embeddings:
        def embed_query(self, _):
            if stage == "embedding":
                block()
            return [0.1]

        def close(self):
            if stage == "embedding":
                closed.set()

    def vectors(*_):
        if stage == "vectors":
            try:
                block()
            finally:
                closed.set()
        return [(note_id, 2, 0.9)]

    factory = Mock(side_effect=Embeddings)
    validate = AsyncMock()
    monkeypatch.setattr(search_service, "_embedding_model_factory", factory)
    monkeypatch.setattr(search_service, "_vector_search", vectors)
    monkeypatch.setattr(search_service._repository, "find_completed_notes", validate)
    kwargs = {
        "headers": {"Authorization": f"Bearer {token}"},
        "json": {"query": "thought"},
    }
    task = asyncio.create_task(
        client.post(f"/v1/projects/{project_id}/search", **kwargs)
    )
    try:
        await event(started)
        response = await asyncio.wait_for(task, 1)
        assert response.status_code == 503
        assert response.json() == {
            "detail": "Semantic search is temporarily unavailable"
        }
        assert not closed.is_set()
        # Busy admission must not create another SDK client or wait for a slot.
        other = await asyncio.wait_for(
            client.post(f"/v1/projects/{project_id}/search", **kwargs), 1
        )
        assert other.status_code == 503
        assert factory.call_count == 1
        validate.assert_not_called()
        assert (await client.get("/health")).status_code == 200
    finally:
        release.set()
        await event(closed)
        await task
        pool.close()
    validate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["json", "sse"])
async def test_chat_timeout_discards_late_generation_and_keeps_social_available(
    chat_api, monkeypatch, transport
):
    client, sessions, _, project_id, _, note_id, _, token, _ = chat_api
    started, release, closed = Event(), Event(), Event()
    pool = BoundedExecutor(1, "chat-test")
    monkeypatch.setattr(chat_service, "_run_blocking", pool.run)
    monkeypatch.setenv("FIKRAH_CHAT_TIMEOUT_SECONDS", "0.1")
    matches = [
        SearchMatch(note_id, "Title", "Content", 2, 0.9, datetime.now(timezone.utc))
    ]
    monkeypatch.setattr(search_service, "retrieve", AsyncMock(return_value=matches))

    class Generator:
        def generate_structured(self, **_):
            started.set()
            assert release.wait(3)
            return ChatAnswer(
                answer="Late answer", supported=True, source_note_ids=[str(note_id)]
            )

        def stream_structured(self, **_):
            yield (
                '{"supported":true,"source_note_ids":["'
                + str(note_id)
                + '"],"answer":"Preview'
            )
            started.set()
            assert release.wait(3)
            yield ' late answer"}'

        def close(self):
            closed.set()

    factory = Mock(side_effect=Generator)
    monkeypatch.setattr(chat_service, "_text_generator_factory", factory)
    headers = {"Authorization": f"Bearer {token}"}
    conversation = (
        await client.post(
            f"/v1/projects/{project_id}/conversations", headers=headers, json={}
        )
    ).json()["id"]
    path = f"/v1/conversations/{conversation}/messages" + (
        "/stream" if transport == "sse" else ""
    )
    task = asyncio.create_task(
        client.post(path, headers=headers, json={"content": "What did I save?"})
    )
    try:
        await event(started)
        response = await asyncio.wait_for(task, 1)
        if transport == "json":
            assert response.status_code == 503
            assert response.json() == {"detail": "LLM chat is temporarily unavailable"}
        else:
            assert response.status_code == 200
            events = test_chat_streaming.parse_events(response)
            assert events[0] == ("delta", {"content": "Preview"})
            assert events[-1] == (
                "error",
                {"status": 503, "detail": "LLM chat is temporarily unavailable"},
            )
            assert all(name != "complete" for name, _ in events)
        assert not closed.is_set()
        # A non-social message gets overload 503 without another paid call.
        rejected = await client.post(
            f"/v1/conversations/{conversation}/messages",
            headers=headers,
            json={"content": "Another question"},
        )
        assert rejected.status_code == 503
        assert factory.call_count == 1
        history = (
            await client.get(f"/v1/conversations/{conversation}", headers=headers)
        ).json()
        assert history["messages"] == []
        social = await client.post(
            f"/v1/conversations/{conversation}/messages",
            headers=headers,
            json={"content": "hello"},
        )
        assert social.status_code == 200
        assert social.json()["assistant_message"]["supported"] is None
        assert social.json()["assistant_message"]["sources"] == []
    finally:
        release.set()
        await event(closed)
        await task
        pool.close()
    async with sessions() as db:
        messages = list(
            await db.scalars(
                select(ChatMessage).where(
                    ChatMessage.conversation_id == UUID(conversation)
                )
            )
        )
        assert len(messages) == 2 and {message.role for message in messages} == {
            "user",
            "assistant",
        }
        assert not any("Late answer" in message.content for message in messages)


@pytest.mark.asyncio
async def test_refusal_uses_remaining_chat_budget_not_a_fresh_deadline(
    chat_api, monkeypatch
):
    client, _, _, project_id, _, note_id, _, token, _ = chat_api
    pool = BoundedExecutor(1, "chat-test")
    started, release, closed = Event(), Event(), Event()
    budgets = []
    monkeypatch.setenv("FIKRAH_CHAT_TIMEOUT_SECONDS", "0.3")
    monkeypatch.setattr(chat_service, "_run_blocking", pool.run)
    monkeypatch.setattr(search_service, "retrieve", AsyncMock(return_value=[]))

    class Generator:
        def generate_structured(self, **_):
            budgets.append(remaining_time(90))
            if len(budgets) == 1:
                # Spend part of the budget, then request a citation repair.
                Event().wait(0.1)
                return ChatAnswer(
                    answer="Invalid citation",
                    supported=True,
                    source_note_ids=[str(note_id)],
                )
            started.set()
            assert release.wait(3)
            return ChatAnswer(answer="Late refusal", supported=False)

        def close(self):
            if len(budgets) == 2:
                closed.set()

    monkeypatch.setattr(chat_service, "_text_generator_factory", Generator)
    headers = {"Authorization": f"Bearer {token}"}
    conversation = (
        await client.post(
            f"/v1/projects/{project_id}/conversations", headers=headers, json={}
        )
    ).json()["id"]
    task = asyncio.create_task(
        client.post(
            f"/v1/conversations/{conversation}/messages",
            headers=headers,
            json={"content": "What did I save?"},
        )
    )
    try:
        await event(started)
        response = await asyncio.wait_for(task, 1)
        assert response.status_code == 503
        assert 0 < budgets[1] < budgets[0] <= 0.3
        assert not closed.is_set()
    finally:
        release.set()
        await event(closed)
        await task
        pool.close()
    history = (
        await client.get(f"/v1/conversations/{conversation}", headers=headers)
    ).json()
    assert history["messages"] == []


@pytest.mark.asyncio
async def test_chat_budget_also_bounds_retrieval(chat_api, monkeypatch):
    client, _, _, project_id, _, _, _, token, _ = chat_api
    monkeypatch.setenv("FIKRAH_CHAT_TIMEOUT_SECONDS", "0.05")
    stopped = asyncio.Event()

    async def retrieve(*_):
        try:
            await asyncio.sleep(5)
        finally:
            stopped.set()

    monkeypatch.setattr(search_service, "retrieve", retrieve)
    factory = Mock(side_effect=AssertionError("No generation after timeout"))
    monkeypatch.setattr(chat_service, "_text_generator_factory", factory)
    headers = {"Authorization": f"Bearer {token}"}
    conversation = (
        await client.post(
            f"/v1/projects/{project_id}/conversations", headers=headers, json={}
        )
    ).json()["id"]
    response = await asyncio.wait_for(
        client.post(
            f"/v1/conversations/{conversation}/messages",
            headers=headers,
            json={"content": "A question"},
        ),
        1,
    )
    assert response.status_code == 503 and stopped.is_set()
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_final_persistence_is_outside_generation_timeout(chat_api, monkeypatch):
    client, _, _, project_id, _, _, _, token, _ = chat_api
    monkeypatch.setenv("FIKRAH_CHAT_TIMEOUT_SECONDS", "0.02")
    original = chat_service._save_exchange

    async def save(*args, **kwargs):
        assert remaining_time(90) == 90  # execution context was restored
        await asyncio.sleep(0.04)
        return await original(*args, **kwargs)

    monkeypatch.setattr(chat_service, "_save_exchange", save)
    headers = {"Authorization": f"Bearer {token}"}
    conversation = (
        await client.post(
            f"/v1/projects/{project_id}/conversations", headers=headers, json={}
        )
    ).json()["id"]
    response = await client.post(
        f"/v1/conversations/{conversation}/messages",
        headers=headers,
        json={"content": "hello"},
    )
    assert response.status_code == 200
