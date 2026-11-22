"""Decode only answer text from a bounded provider JSON stream."""

from collections.abc import Callable, Iterator
from contextlib import closing

from pydantic_core import from_json

from app.llm.contracts import ChatAnswer

MAX_CHAT_OUTPUT_CHARACTERS = 64_000


def consume_chat_stream(
    chunks: Iterator[str],
    allowed_source_ids: set[str],
    on_delta: Callable[[str], None],
) -> ChatAnswer:
    """Publish provisional text only after a supported, in-scope citation header.

    PostgreSQL revision/conversation checks still run before the final response
    is saved. Clients must replace previews with the final persisted response.
    """
    output = ""
    emitted = ""
    header: tuple[str, ...] | None = None
    with closing(chunks):
        for chunk in chunks:
            output += chunk
            if len(output) > MAX_CHAT_OUTPUT_CHARACTERS:
                raise ValueError("Chat output exceeded its size limit")
            try:
                partial = from_json(output, allow_partial="trailing-strings")
            except ValueError:
                continue
            if not isinstance(partial, dict):
                continue
            # Requiring answer last means the citation array has finished before
            # any decoded text can be shown. Unknown IDs never open the gate.
            ids = partial.get("source_note_ids")
            if (
                list(partial) != ["supported", "source_note_ids", "answer"]
                or partial.get("supported") is not True
                or not isinstance(ids, list)
                or not ids
                or not all(
                    isinstance(item, str) and item in allowed_source_ids for item in ids
                )
                or not isinstance(partial.get("answer"), str)
            ):
                if emitted:
                    raise ValueError("Chat stream changed its support header")
                continue
            current_header = tuple(ids)
            if header is not None and header != current_header:
                raise ValueError("Chat stream changed its sources")
            header = current_header
            text = partial["answer"]
            if not text.startswith(emitted):
                raise ValueError("Chat stream revised text already emitted")
            delta = text[len(emitted) :]
            if delta:
                on_delta(delta)
                emitted = text

    answer = ChatAnswer.model_validate_json(output)
    if emitted and (
        not answer.supported
        or tuple(answer.source_note_ids) != header
        or answer.answer != emitted
    ):
        raise ValueError("Final chat answer differs from its preview")
    return answer
