"""Prepare conversation history and overview context independently of generation."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.search import SearchMatch
from app.llm.contracts import ChatHistoryMessage, ChatNoteContext
from app.repositories.chat import ChatRepository
from app.services.chat_policy import is_continuation, is_note_overview, social_reply

CHAT_CONTEXT_LIMIT = 8
CHAT_HISTORY_LIMIT = 12
CHAT_OVERVIEW_BATCH_SIZE = 3


@dataclass(frozen=True, slots=True)
class ChatContext:
    retrieval_query: str
    overview: bool
    shown_note_ids: frozenset[UUID]
    history: list[ChatHistoryMessage]
    last_message_id: UUID | None

    def select_matches(self, matches: list[SearchMatch]) -> list[SearchMatch]:
        if not self.overview:
            return matches
        return [match for match in matches if match.note_id not in self.shown_note_ids][
            :CHAT_OVERVIEW_BATCH_SIZE
        ]


async def load_chat_context(
    repository: ChatRepository,
    db: AsyncSession,
    conversation_id: UUID,
    question: str,
) -> ChatContext:
    history_desc = await repository.list_recent_messages(db, conversation_id, CHAT_HISTORY_LIMIT)
    history = [
        ChatHistoryMessage(role=message.role, content=message.content)
        for message in reversed(history_desc)
    ]
    retrieval_query = question
    overview = is_note_overview(question)
    shown_ids: set[UUID] = set()
    if is_continuation(question):
        anchor = next(
            (
                message for message in history_desc
                if message.role == "user"
                and not is_continuation(message.content)
                and social_reply(message.content) is None
            ),
            None,
        )
        if anchor is not None and is_note_overview(anchor.content):
            retrieval_query = anchor.content
            overview = True
            page_history = []
            for message in history_desc:
                if message.id == anchor.id:
                    break
                page_history.append(message)
            # Use persisted citations, never source claims in conversation text.
            if page_history:
                rows = await repository.list_message_sources(db, page_history)
                shown_ids = {note_id for _message_id, note_id, _title in rows}
    return ChatContext(
        retrieval_query=retrieval_query,
        overview=overview,
        shown_note_ids=frozenset(shown_ids),
        history=history,
        last_message_id=history_desc[0].id if history_desc else None,
    )


def note_context(matches: list[SearchMatch]) -> list[ChatNoteContext]:
    return [
        ChatNoteContext(
            note_id=str(match.note_id), title=match.title or "Untitled note", content=match.content
        )
        for match in matches
    ]
