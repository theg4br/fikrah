"""Real incremental decoding, cancellation and project-chat SSE boundaries."""

import asyncio
import json
from contextlib import aclosing
from threading import Event
from unittest.mock import AsyncMock, Mock

import pytest

from app.contracts.chat import ChatCompleted, ChatDelta
from app.infrastructure.container import chat_service, request_budgets, search_service
from app.llm.contracts import ChatAnswer
from app.llm.streaming import consume_chat_stream
from app.services.chat_stream import stream_exchange
from app.services.request_budgets import RequestBudgetExceeded, RequestBudgetUnavailable
from tests import test_chat_api

chat_api = test_chat_api.chat_api


def pieces(text, size=1):
    for start in range(0, len(text), size):
        yield text[start : start + size]


@pytest.mark.parametrize("size", [1, 2, 7, 1000])
def test_stream_decodes_only_answer_text_after_source_header(size):
    value = ChatAnswer(
        supported=True, source_note_ids=["note-id"], answer='قال: "مرحبا"\nC:\\notes 😀'
    )
    output = json.dumps(value.model_dump(), ensure_ascii=True)
    deltas = []
    result = consume_chat_stream(pieces(output, size), {"note-id"}, deltas.append)
    assert result == value
    assert "".join(deltas) == value.answer
    if size == 1:
        assert len(deltas) > 3


@pytest.mark.parametrize(
    "ids,supported",
    [
        ([], True),
        (["private-id"], True),
        (["note-id", "private-id"], True),
        ([], False),
    ],
)
def test_invalid_or_unsupported_headers_never_release_deltas(ids, supported):
    value = ChatAnswer(
        supported=supported, source_note_ids=ids, answer="Buffered until validation"
    )
    emit = Mock()
    assert (
        consume_chat_stream(pieces(value.model_dump_json()), {"note-id"}, emit) == value
    )
    emit.assert_not_called()


def test_stream_is_incremental_and_closes_on_consumer_failure():
    closed = []

    def upstream():
        try:
            yield '{"supported":true,"source_note_ids":["n"],"answer":"First'
            raise AssertionError("Consumer should have stopped the provider")
        finally:
            closed.append(True)

    def stop(_):
        raise RuntimeError("Disconnected")

    with pytest.raises(RuntimeError, match="Disconnected"):
        consume_chat_stream(upstream(), {"n"}, stop)
    assert closed == [True]


@pytest.mark.parametrize(
    "output",
    [
        '{"supported":true,"source_note_ids":["n"],"answer":"first","answer":"different"}',
        '{"supported":true,"source_note_ids":["n"],"answer":"text","supported":false}',
        '{"supported":true,"source_note_ids":["n"],"answer":"text","source_note_ids":["private"]}',
        '{"supported":true,"source_note_ids":["n"],"answer":"unfinished',
    ],
)
def test_truncated_or_revised_stream_cannot_be_committed(output):
    with pytest.raises(ValueError):
        consume_chat_stream(pieces(output), {"n"}, lambda _: None)


def test_stream_rejects_oversize_output():
    with pytest.raises(ValueError, match="size limit"):
        consume_chat_stream(pieces("x" * 64001, 64001), set(), lambda _: None)


@pytest.mark.asyncio
async def test_async_bridge_yields_while_provider_is_still_generating():
    release = Event()

    async def operation(emit):
        def generate():
            emit("First")
            assert release.wait(3)
            emit(" second")

        await asyncio.to_thread(generate)
        return "persisted result"

    async with aclosing(stream_exchange(operation)) as stream:
        assert await asyncio.wait_for(anext(stream), 2) == ChatDelta("First")
        release.set()
        assert [event async for event in stream] == [
            ChatDelta(" second"),
            ChatCompleted("persisted result"),
        ]


@pytest.mark.asyncio
async def test_disconnected_consumer_unblocks_a_full_queue_and_closes_provider():
    closed = Event()

    async def operation(emit):
        def generate():
            try:
                for _ in range(1000):
                    emit("token")
            finally:
                closed.set()

        await asyncio.to_thread(generate)

    stream = stream_exchange(operation)
    assert isinstance(await asyncio.wait_for(anext(stream), 2), ChatDelta)
    await asyncio.wait_for(stream.aclose(), 2)
    assert await asyncio.to_thread(closed.wait, 2)


def parse_events(response):
    return [
        (
            block.splitlines()[0].removeprefix("event: "),
            json.loads(block.splitlines()[1].removeprefix("data: ")),
        )
        for block in response.text.strip().split("\n\n")
    ]


@pytest.fixture
def stream_api(chat_api, monkeypatch):
    (
        client,
        sessions,
        _owner,
        project,
        _other_project,
        note,
        private_note,
        token,
        other_token,
    ) = chat_api
    monkeypatch.setattr(
        search_service, "_embedding_model_factory", test_chat_api.FakeEmbeddingModel
    )
    monkeypatch.setattr(
        search_service,
        "_vector_search",
        lambda *_: [(note, 2, 0.95), (private_note, 1, 0.9)],
    )
    return client, sessions, project, note, token, other_token


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    ["supported", "unsupported", "invalid_source", "provider_error", "changed_note"],
)
async def test_stream_api_preserves_grounding_and_atomic_persistence(
    stream_api, monkeypatch, scenario
):
    client, _sessions, project, note, token, _ = stream_api
    closed = []

    class Generator:
        def close(self):
            closed.append(True)

        def stream_structured(self, **kwargs):
            request = json.loads(kwargs["user_content"])
            assert [n["note_id"] for n in request["project_notes"]] == [str(note)]
            assert "outside explanations" in kwargs["system_instruction"]
            yield '{"supported":'
            if scenario == "provider_error":
                raise RuntimeError("PRIVATE_PROVIDER_ERROR")
            supported = scenario != "unsupported"
            ids = (
                ["private"]
                if scenario == "invalid_source"
                else ([str(note)] if supported else [])
            )
            value = ChatAnswer(
                supported=supported,
                source_note_ids=ids,
                answer="A reliable source."
                if supported
                else "The project notes do not support this answer.",
            )
            yield from pieces(value.model_dump_json()[len('{"supported":') :], 8)

        def generate_structured(self, **kwargs):
            assert json.loads(kwargs["user_content"])["project_notes"] == []
            return ChatAnswer(supported=False, answer="No current supported answer.")

    monkeypatch.setattr(chat_service, "_text_generator_factory", Generator)
    if scenario == "changed_note":

        async def changed(*_):
            return []

        monkeypatch.setattr(
            chat_service._repository, "find_completed_notes_for_citations", changed
        )
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        f"/v1/projects/{project}/conversations", headers=headers, json={}
    )
    conversation = created.json()["id"]
    response = await client.post(
        f"/v1/conversations/{conversation}/messages/stream",
        headers=headers,
        json={"content": "What did I save?"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    events = parse_events(response)
    assert "PRIVATE_PROVIDER_ERROR" not in response.text
    history = (
        await client.get(f"/v1/conversations/{conversation}", headers=headers)
    ).json()
    if scenario == "provider_error":
        assert events[-1][0] == "error"
        assert events[-1][1]["status"] == 503
        assert history["messages"] == []
    else:
        assert events[-1][0] == "complete"
        answer = events[-1][1]["assistant_message"]
        assert history["messages"][-1] == answer
        assert answer["supported"] is (scenario == "supported")
        assert bool(answer["sources"]) is (scenario == "supported")
        if scenario == "supported":
            assert (
                "".join(data["content"] for event, data in events if event == "delta")
                == answer["content"]
            )
            assert len(events) > 2
        if scenario in {"invalid_source", "unsupported"}:
            assert [event for event, _ in events] == ["complete"]
    assert closed


@pytest.mark.asyncio
async def test_stream_social_and_access_control_keep_existing_http_errors(
    stream_api, monkeypatch
):
    client, _, project, _, token, other_token = stream_api
    factory = Mock(side_effect=AssertionError("No social provider"))
    monkeypatch.setattr(chat_service, "_text_generator_factory", factory)
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        f"/v1/projects/{project}/conversations", headers=headers, json={}
    )
    path = f"/v1/conversations/{created.json()['id']}/messages/stream"
    assert (await client.post(path, json={"content": "hello"})).status_code == 401
    assert (
        await client.post(
            path,
            headers={"Authorization": f"Bearer {other_token}"},
            json={"content": "hello"},
        )
    ).status_code == 404
    assert (
        await client.post(path, headers=headers, json={"content": " "})
    ).status_code == 422
    response = await client.post(path, headers=headers, json={"content": "انت مين"})
    events = parse_events(response)
    assert events[-1][1]["assistant_message"]["supported"] is None
    assert events[-1][1]["assistant_message"]["sources"] == []
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["/messages", "/messages/stream"])
@pytest.mark.parametrize("outage", [False, True])
async def test_both_chat_transports_enforce_the_same_budget_before_generation(
    stream_api, monkeypatch, suffix, outage
):
    client, _, project, _, token, _ = stream_api
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        f"/v1/projects/{project}/conversations", headers=headers, json={}
    )
    consume = AsyncMock(
        side_effect=RequestBudgetUnavailable() if outage else RequestBudgetExceeded(42)
    )
    monkeypatch.setattr(request_budgets, "llm", consume)
    response = await client.post(
        f"/v1/conversations/{created.json()['id']}{suffix}",
        headers=headers,
        json={"content": "hello"},
    )
    assert response.status_code == (503 if outage else 429)
    assert response.headers["content-type"].startswith("application/json")
    if not outage:
        assert response.headers["retry-after"] == "42"
    consume.assert_awaited_once()
