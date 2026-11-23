"""Composition root for application services and infrastructure adapters."""

from app.core.config import get_settings
from app.infrastructure.email.cipher import protect_email_token, reveal_email_token
from app.infrastructure.email.smtp import send_action_email, send_welcome_email
from app.infrastructure.execution import BoundedExecutor
from app.infrastructure.identity.google import verify_google_id_token
from app.infrastructure.rate_limits import RedisBudgetStore
from app.infrastructure.task_dispatch import notify_pending_work
from app.infrastructure.vector.qdrant import search_note_vectors
from app.llm.factory import LLMProviderFactory
from app.services.auth_service import AuthService
from app.services.chat_service import ChatService
from app.services.connections_service import KnowledgeConnectionService
from app.services.notes_service import NoteService
from app.services.projects_service import ProjectService
from app.services.request_budgets import RequestBudgets
from app.services.search_service import SemanticSearchService
from app.services.transcription_service import TranscriptionService

request_budgets = RequestBudgets(
    RedisBudgetStore(lambda: get_settings().redis_url),
    login_limit=lambda: get_settings().fikrah_login_limit_per_minute,
    llm_limit=lambda: get_settings().fikrah_llm_limit_per_minute,
    google_limit=lambda: get_settings().fikrah_google_limit_per_minute,
)

search_executor = BoundedExecutor(get_settings().fikrah_search_executor_workers, "search")
text_executor = BoundedExecutor(get_settings().fikrah_llm_executor_workers, "text")
google_executor = BoundedExecutor(get_settings().fikrah_google_executor_workers, "google")


def close_api_executors() -> None:
    search_executor.close()
    text_executor.close()
    google_executor.close()

auth_service = AuthService(
    send_action_email=send_action_email,
    protect_email_token=protect_email_token,
    reveal_email_token=reveal_email_token,
    send_welcome_email=send_welcome_email,
    verify_google_id_token=verify_google_id_token,
    google_client_id=lambda: get_settings().google_client_id,
    login_budget=request_budgets.login,
    run_google_verification=google_executor.run,
)
search_service = SemanticSearchService(
    embedding_model_factory=LLMProviderFactory.create_embedding_model,
    vector_search=search_note_vectors,
    run_blocking=search_executor.run,
)
chat_service = ChatService(
    search_service=search_service,
    text_generator_factory=LLMProviderFactory.create_text_generator,
    run_blocking=text_executor.run,
)
note_service = NoteService(notify_processing=notify_pending_work)
project_service = ProjectService()
knowledge_connection_service = KnowledgeConnectionService()
transcription_service = TranscriptionService(
    transcriber_factory=LLMProviderFactory.create_audio_transcriber,
    max_audio_bytes=lambda: get_settings().fikrah_transcription_max_bytes,
)
