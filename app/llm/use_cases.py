"""Application-level LLM workflows and prompt composition."""

from collections.abc import Callable
from time import perf_counter

from app.core.telemetry import observe, timed
from app.llm.contracts import (
    ChatAnswer,
    ChatHistoryMessage,
    ChatNoteContext,
    NoteAnalysis,
)
from app.llm.ports import StructuredTextGenerator
from app.llm.prompts import (
    CHAT_STREAM_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
    NOTE_ANALYSIS_SYSTEM_PROMPT,
    build_chat_prompt,
    build_note_analysis_prompt,
)
from app.llm.streaming import consume_chat_stream


@timed("llm.note_analysis")
def analyze_note(text_generator: StructuredTextGenerator, content: str) -> NoteAnalysis:
    return text_generator.generate_structured(
        system_instruction=NOTE_ANALYSIS_SYSTEM_PROMPT,
        user_content=build_note_analysis_prompt(content),
        response_model=NoteAnalysis,
    )


@timed("llm.chat_generation")
def answer_project_question(
    text_generator: StructuredTextGenerator,
    question: str,
    notes: list[ChatNoteContext],
    history: list[ChatHistoryMessage],
    *,
    on_delta: Callable[[str], None] | None = None,
) -> ChatAnswer:
    if on_delta is not None:
        started = perf_counter()
        first_delta = True

        def emit(content: str) -> None:
            nonlocal first_delta
            on_delta(content)
            if first_delta:
                first_delta = False
                observe("chat.generation_first_delta", perf_counter() - started)

        return consume_chat_stream(
            text_generator.stream_structured(
                system_instruction=CHAT_STREAM_SYSTEM_PROMPT,
                user_content=build_chat_prompt(question, notes, history),
                response_model=ChatAnswer,
            ),
            {note.note_id for note in notes},
            emit,
        )
    return text_generator.generate_structured(
        system_instruction=CHAT_SYSTEM_PROMPT,
        user_content=build_chat_prompt(question, notes, history),
        response_model=ChatAnswer,
    )
