"""Project chat use cases, retrieval orchestration, and persistence rules."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.chat import (
    ConversationCreateCommand,
    ConversationHistory,
    ConversationSummary,
    MessageCreateCommand,
    MessageRecord,
    MessageSubmissionResult,
    SourceReference,
)
from app.core.config import get_settings
from app.core.execution import BlockingRunner, execution_budget
from app.core.telemetry import timed
from app.llm.lifecycle import run_with_provider
from app.llm.ports import StructuredTextGenerator
from app.llm.use_cases import answer_project_question
from app.models import ChatConversation, ChatMessage, Project
from app.rag.errors import SearchUnavailableError
from app.repositories.chat import ChatRepository
from app.services.chat_context import (
    CHAT_CONTEXT_LIMIT,
    load_chat_context,
    note_context,
)
from app.services.chat_errors import ChatFailure, ChatServiceError
from app.services.chat_policy import social_reply
from app.services.search_errors import SearchProjectNotFound
from app.services.search_service import SemanticSearchService


@dataclass(frozen=True)
class _PreparedReply:
    conversation: ChatConversation
    content: str
    supported: bool | None
    sources: list[UUID]


class ChatService:
    def __init__(
        self,
        search_service: SemanticSearchService,
        text_generator_factory: Callable[[], StructuredTextGenerator],
        repository: ChatRepository | None = None,
        *,
        run_blocking: BlockingRunner = asyncio.to_thread,
    ) -> None:
        self._search_service = search_service
        self._text_generator_factory = text_generator_factory
        self._repository = repository or ChatRepository()
        self._run_blocking = run_blocking

    async def _owned_project(
        self, db: AsyncSession, user_id: UUID, project_id: UUID, *, lock: bool = False
    ) -> Project:
        project = await self._repository.find_owned_project(
            db, user_id, project_id, lock=lock
        )
        if project is None:
            raise ChatServiceError(ChatFailure.PROJECT_NOT_FOUND, "Project not found")
        return project

    async def _owned_conversation(
        self,
        db: AsyncSession,
        user_id: UUID,
        conversation_id: UUID,
        *,
        lock: bool = False,
    ) -> ChatConversation:
        conversation = await self._repository.find_owned_conversation(
            db, user_id, conversation_id, lock=lock
        )
        if conversation is None:
            raise ChatServiceError(
                ChatFailure.CONVERSATION_NOT_FOUND, "Conversation not found"
            )
        return conversation

    async def _message_views(
        self, db: AsyncSession, messages: list[ChatMessage]
    ) -> list[MessageRecord]:
        if not messages:
            return []
        rows = await self._repository.list_message_sources(db, messages)
        sources_by_message: dict[UUID, list[SourceReference]] = {}
        for message_id, note_id, title in rows:
            sources_by_message.setdefault(message_id, []).append(
                SourceReference(note_id=note_id, title=title)
            )
        return [
            MessageRecord(
                id=message.id,
                role=message.role,
                content=message.content,
                supported=message.supported,
                sources=tuple(sources_by_message.get(message.id, [])),
                created_at=message.created_at,
            )
            for message in messages
        ]

    async def list_conversations(
        self, db: AsyncSession, user_id: UUID, project_id: UUID
    ) -> list[ConversationSummary]:
        await self._owned_project(db, user_id, project_id)
        rows = await self._repository.list_conversations(db, user_id, project_id)
        return [self._conversation_summary(item) for item in rows]

    async def create_conversation(
        self,
        db: AsyncSession,
        user_id: UUID,
        project_id: UUID,
        body: ConversationCreateCommand,
    ) -> ConversationSummary:
        await self._owned_project(db, user_id, project_id, lock=True)
        conversation = ChatConversation(
            user_id=user_id,
            project_id=project_id,
            title=body.title,
        )
        await self._repository.add_conversation(db, conversation)
        await db.commit()
        await self._repository.refresh_conversation(db, conversation)
        return self._conversation_summary(conversation)

    async def get_conversation(
        self, db: AsyncSession, user_id: UUID, conversation_id: UUID
    ) -> ConversationHistory:
        conversation = await self._owned_conversation(db, user_id, conversation_id)
        messages = await self._repository.list_messages(db, conversation.id)
        return ConversationHistory(
            conversation=self._conversation_summary(conversation),
            messages=tuple(await self._message_views(db, messages)),
        )

    @timed("chat.total")
    async def send_message(
        self,
        db: AsyncSession,
        user_id: UUID,
        conversation_id: UUID,
        body: MessageCreateCommand,
        *,
        on_delta: Callable[[str], None] | None = None,
    ) -> MessageSubmissionResult:
        try:
            async with execution_budget(get_settings().fikrah_chat_timeout_seconds):
                reply = await self._prepare_reply(
                    db, user_id, conversation_id, body, on_delta=on_delta
                )
        except TimeoutError as error:
            await db.rollback()
            raise ChatServiceError(
                ChatFailure.GENERATION_UNAVAILABLE,
                "LLM chat is temporarily unavailable",
            ) from error
        # Do not cancel the final commit on a generation deadline. A client must
        # never receive a deadline error while this code continues to save.
        return await self._save_exchange(
            db, reply.conversation, body, reply.content,
            supported=reply.supported, valid_sources=reply.sources,
        )

    async def _prepare_reply(
        self,
        db: AsyncSession,
        user_id: UUID,
        conversation_id: UUID,
        body: MessageCreateCommand,
        *,
        on_delta: Callable[[str], None] | None,
    ) -> _PreparedReply:
        conversation = await self._owned_conversation(
            db, user_id, conversation_id, lock=True
        )
        greeting = social_reply(body.content)
        if greeting is not None:
            return _PreparedReply(conversation, greeting, None, [])
        context = await load_chat_context(
            self._repository, db, conversation.id, body.content
        )
        history = context.history
        project_id = conversation.project_id
        last_message_id = context.last_message_id
        await db.rollback()

        try:
            matches = await self._search_service.retrieve(
                db, user_id, project_id, context.retrieval_query, CHAT_CONTEXT_LIMIT
            )
        except SearchProjectNotFound as error:
            raise ChatServiceError(
                ChatFailure.PROJECT_NOT_FOUND, "Project not found"
            ) from error
        except SearchUnavailableError as error:
            raise ChatServiceError(
                ChatFailure.KNOWLEDGE_UNAVAILABLE,
                "Project knowledge is temporarily unavailable",
            ) from error

        matches = context.select_matches(matches)
        notes = note_context(matches)
        matching_revisions = {match.note_id: match.revision for match in matches}
        try:
            answer = await self._run_blocking(
                run_with_provider,
                self._text_generator_factory,
                lambda generator: answer_project_question(
                    generator, body.content, notes, history, on_delta=on_delta
                ),
            )
        except Exception as error:
            raise ChatServiceError(
                ChatFailure.GENERATION_UNAVAILABLE,
                "LLM chat is temporarily unavailable",
            ) from error

        conversation = await self._owned_conversation(
            db, user_id, conversation_id, lock=True
        )
        latest_message_id = await self._repository.latest_message_id(
            db, conversation.id
        )
        if latest_message_id != last_message_id:
            await db.rollback()
            raise ChatServiceError(
                ChatFailure.CONVERSATION_CHANGED,
                "The conversation changed while the answer was being generated. Retry the message.",
            )

        available_ids = set(matching_revisions)
        valid_sources: list[UUID] = []
        if answer.supported:
            for source_id in answer.source_note_ids:
                try:
                    parsed_id = UUID(source_id)
                except (TypeError, ValueError):
                    continue
                if parsed_id in available_ids and parsed_id not in valid_sources:
                    valid_sources.append(parsed_id)

        sources_changed = False
        if valid_sources:
            source_notes = await self._repository.find_completed_notes_for_citations(
                db, user_id, project_id, valid_sources
            )
            current_source_ids = {
                note.id
                for note in source_notes
                if note.revision == matching_revisions.get(note.id)
            }
            sources_changed = any(
                note_id not in current_source_ids for note_id in valid_sources
            )
            valid_sources = [
                note_id for note_id in valid_sources if note_id in current_source_ids
            ]

        supported = answer.supported and bool(valid_sources) and not sources_changed
        answer_content = answer.answer.strip()
        if (answer.supported and not supported) or not answer_content:
            # A generated answer without a valid current citation is not safe to
            # show. Ask the same model for a localized refusal with no evidence
            # available, instead of returning a fixed English fallback.
            await db.rollback()
            try:
                refusal = await self._run_blocking(
                    run_with_provider,
                    self._text_generator_factory,
                    lambda generator: answer_project_question(
                        generator, body.content, [], history
                    ),
                )
            except Exception as error:
                raise ChatServiceError(
                    ChatFailure.GENERATION_UNAVAILABLE,
                    "LLM chat is temporarily unavailable",
                ) from error
            answer_content = refusal.answer.strip()
            if refusal.supported or not answer_content:
                raise ChatServiceError(
                    ChatFailure.INVALID_MODEL_RESPONSE,
                    "LLM could not produce a supported chat response",
                )
            # The refusal generation may take time. Reacquire the conversation
            # lock and reject the response if another message arrived meanwhile.
            conversation = await self._owned_conversation(
                db, user_id, conversation_id, lock=True
            )
            latest_message_id = await self._repository.latest_message_id(
                db, conversation.id
            )
            if latest_message_id != last_message_id:
                await db.rollback()
                raise ChatServiceError(
                    ChatFailure.CONVERSATION_CHANGED,
                    "The conversation changed while the answer was being generated. Retry the message.",
                )
            supported = False
            valid_sources = []

        return _PreparedReply(conversation, answer_content, supported, valid_sources)

    async def authorize_conversation(
        self, db: AsyncSession, user_id: UUID, conversation_id: UUID
    ) -> None:
        """Check access before HTTP streaming starts, then release the read."""
        await self._owned_conversation(db, user_id, conversation_id)
        await db.rollback()

    @timed("chat.persistence")
    async def _save_exchange(
        self,
        db: AsyncSession,
        conversation: ChatConversation,
        body: MessageCreateCommand,
        answer_content: str,
        *,
        supported: bool | None,
        valid_sources: list[UUID],
    ) -> MessageSubmissionResult:
        now = datetime.now(timezone.utc)
        conversation.updated_at = now
        user_message = ChatMessage(
            conversation_id=conversation.id,
            role="user",
            content=body.content,
            created_at=now,
        )
        assistant_message = ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=answer_content,
            supported=supported,
            created_at=now + timedelta(microseconds=1),
        )
        if conversation.title is None:
            conversation.title = body.content[:120]
        await self._repository.add_messages_with_sources(
            db,
            user_message,
            assistant_message,
            valid_sources,
            supported=bool(supported),
        )
        await db.commit()
        await self._repository.refresh_conversation(db, conversation)
        await self._repository.refresh_message(db, user_message)
        await self._repository.refresh_message(db, assistant_message)

        user_view, assistant_view = await self._message_views(
            db, [user_message, assistant_message]
        )
        return MessageSubmissionResult(
            conversation=self._conversation_summary(conversation),
            user_message=user_view,
            assistant_message=assistant_view,
        )

    @staticmethod
    def _conversation_summary(
        conversation: ChatConversation,
    ) -> ConversationSummary:
        return ConversationSummary(
            id=conversation.id,
            project_id=conversation.project_id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )

    async def delete_conversation(
        self, db: AsyncSession, user_id: UUID, conversation_id: UUID
    ) -> None:
        conversation = await self._owned_conversation(
            db, user_id, conversation_id, lock=True
        )
        await self._repository.delete_conversation_with_messages(db, conversation)
        await db.commit()
