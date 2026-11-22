"""HTTP presentation layer for project-scoped knowledge chat."""

import json
import logging
from collections.abc import Awaitable
from contextlib import aclosing
from typing import TypeVar
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import AuthenticatedUser, ChatServiceDependency, Db, LLMRequestBudget
from app.contracts.chat import (
    ChatCompleted,
    ConversationCreateCommand,
    ConversationSummary,
    MessageCreateCommand,
    MessageRecord,
    MessageSubmissionResult,
    SourceReference,
)
from app.schemas.chat import (
    ConversationCreate,
    ConversationDetail,
    ConversationView,
    MessageCreate,
    MessageSubmission,
    MessageView,
    SourceView,
)
from app.services.chat_errors import ChatFailure, ChatServiceError
from app.services.chat_stream import stream_exchange

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)
T = TypeVar("T")

CHAT_FAILURE_STATUSES = {
    ChatFailure.PROJECT_NOT_FOUND: 404,
    ChatFailure.CONVERSATION_NOT_FOUND: 404,
    ChatFailure.KNOWLEDGE_UNAVAILABLE: 503,
    ChatFailure.GENERATION_UNAVAILABLE: 503,
    ChatFailure.CONVERSATION_CHANGED: 409,
    ChatFailure.INVALID_MODEL_RESPONSE: 503,
}


@router.get(
    "/v1/projects/{project_id}/conversations", response_model=list[ConversationView]
)
async def list_conversations(
    project_id: UUID,
    user: AuthenticatedUser,
    db: Db,
    service: ChatServiceDependency,
) -> list[ConversationView]:
    conversations = await _execute(service.list_conversations(db, user.id, project_id))
    return [_conversation_view(item) for item in conversations]


@router.post(
    "/v1/projects/{project_id}/conversations",
    response_model=ConversationView,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    project_id: UUID,
    body: ConversationCreate,
    user: AuthenticatedUser,
    db: Db,
    service: ChatServiceDependency,
) -> ConversationView:
    conversation = await _execute(
        service.create_conversation(
            db,
            user.id,
            project_id,
            ConversationCreateCommand(title=body.title),
        )
    )
    return _conversation_view(conversation)


@router.get("/v1/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    user: AuthenticatedUser,
    db: Db,
    service: ChatServiceDependency,
) -> ConversationDetail:
    conversation = await _execute(
        service.get_conversation(db, user.id, conversation_id)
    )
    return ConversationDetail(
        **_conversation_view(conversation.conversation).model_dump(),
        messages=[_message_view(message) for message in conversation.messages],
    )


@router.post(
    "/v1/conversations/{conversation_id}/messages",
    response_model=MessageSubmission,
)
async def send_message(
    conversation_id: UUID,
    body: MessageCreate,
    user: AuthenticatedUser,
    db: Db,
    budget: LLMRequestBudget,
    service: ChatServiceDependency,
) -> MessageSubmission:
    submission = await _execute(
        service.send_message(
            db,
            user.id,
            conversation_id,
            MessageCreateCommand(content=body.content),
        )
    )
    return _submission_view(submission)


@router.post(
    "/v1/conversations/{conversation_id}/messages/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE delta, complete, or error events",
        }
    },
)
async def stream_message(
    conversation_id: UUID,
    body: MessageCreate,
    user: AuthenticatedUser,
    db: Db,
    budget: LLMRequestBudget,
    service: ChatServiceDependency,
) -> StreamingResponse:
    user_id = user.id
    await _execute(service.authorize_conversation(db, user_id, conversation_id))

    async def events():
        try:
            async with aclosing(
                stream_exchange(
                    lambda emit: service.send_message(
                        db,
                        user_id,
                        conversation_id,
                        MessageCreateCommand(content=body.content),
                        on_delta=emit,
                    )
                )
            ) as stream:
                async for event in stream:
                    if isinstance(event, ChatCompleted):
                        yield _sse(
                            "complete",
                            _submission_view(event.result).model_dump(mode="json"),
                        )
                    else:
                        yield _sse("delta", {"content": event.content})
        except ChatServiceError as error:
            yield _sse(
                "error",
                {"status": CHAT_FAILURE_STATUSES[error.code], "detail": error.detail},
            )
        except Exception:  # noqa: BLE001 - do not expose persistence/provider details over SSE
            logger.warning("Chat stream failed")
            yield _sse(
                "error",
                {
                    "status": 500,
                    "detail": "Chat stream failed. Reload the conversation before retrying.",
                },
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.delete(
    "/v1/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_conversation(
    conversation_id: UUID,
    user: AuthenticatedUser,
    db: Db,
    service: ChatServiceDependency,
) -> Response:
    await _execute(service.delete_conversation(db, user.id, conversation_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _execute(call: Awaitable[T]) -> T:
    try:
        return await call
    except ChatServiceError as error:
        raise HTTPException(
            status_code=CHAT_FAILURE_STATUSES[error.code], detail=error.detail
        ) from error


def _conversation_view(value: ConversationSummary) -> ConversationView:
    return ConversationView(
        id=value.id,
        project_id=value.project_id,
        title=value.title,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _message_view(value: MessageRecord) -> MessageView:
    return MessageView(
        id=value.id,
        role=value.role,
        content=value.content,
        supported=value.supported,
        sources=[_source_view(source) for source in value.sources],
        created_at=value.created_at,
    )


def _source_view(value: SourceReference) -> SourceView:
    return SourceView(note_id=value.note_id, title=value.title)


def _submission_view(value: MessageSubmissionResult) -> MessageSubmission:
    return MessageSubmission(
        conversation=_conversation_view(value.conversation),
        user_message=_message_view(value.user_message),
        assistant_message=_message_view(value.assistant_message),
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
