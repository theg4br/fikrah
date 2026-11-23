"""Chat context extraction preserves retrieval queries and persisted-source paging."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.chat_context import ChatContext, load_chat_context, note_context


@pytest.mark.asyncio
async def test_empty_history_keeps_question_and_does_not_query_sources():
    repository = SimpleNamespace(
        list_recent_messages=AsyncMock(return_value=[]), list_message_sources=AsyncMock()
    )
    conversation_id = uuid4()
    context = await load_chat_context(repository, None, conversation_id, "What did I save?")
    assert context.retrieval_query == "What did I save?"
    assert context.last_message_id is None
    assert context.history == []
    assert context.shown_note_ids == frozenset()
    repository.list_recent_messages.assert_awaited_once_with(None, conversation_id, 12)
    repository.list_message_sources.assert_not_awaited()


@pytest.mark.asyncio
async def test_continuation_uses_anchor_and_only_persisted_citations():
    anchor = SimpleNamespace(id=uuid4(), role="user", content="Summarize my notes")
    answer = SimpleNamespace(id=uuid4(), role="assistant", content="A recorded item")
    shown_note_id = uuid4()
    repository = SimpleNamespace(
        list_recent_messages=AsyncMock(return_value=[answer, anchor]),
        list_message_sources=AsyncMock(return_value=[(answer.id, shown_note_id, "Title")]),
    )
    context = await load_chat_context(repository, None, uuid4(), "continue")
    assert context.retrieval_query == anchor.content
    assert context.overview is True
    assert context.shown_note_ids == {shown_note_id}
    assert context.last_message_id == answer.id
    assert [message.content for message in context.history] == [anchor.content, answer.content]
    repository.list_message_sources.assert_awaited_once_with(None, [answer])


def test_match_selection_retains_existing_three_item_window_and_fallback_title():
    matches = [SimpleNamespace(note_id=uuid4(), title=None, content="Stored") for _ in range(6)]
    context = ChatContext("summary", True, frozenset({matches[0].note_id}), [], None)
    assert context.select_matches(matches) == matches[1:4]
    assert note_context(matches[:1])[0].title == "Untitled note"
    ordinary = ChatContext("question", False, frozenset(), [], None)
    assert ordinary.select_matches(matches) is matches
