# Project Knowledge Chat

## Goal

Let an authenticated user ask questions against notes in one selected project, retain conversation history, and return answer citations that the mobile client can open.

## API

All routes require authentication. The backend scopes each conversation to its owner and project. Mobile clients never submit an owner ID.

### `GET /v1/projects/{project_id}/conversations`

Lists the authenticated user's conversations for the selected project, ordered by most recently updated. The initial API has no pagination.

### `POST /v1/projects/{project_id}/conversations`

Creates an empty conversation. The request may include a user-provided title. When no title is provided, the first user question becomes the title, truncated to 120 characters. A newly created conversation has a `null` title until its first message is sent.

### `GET /v1/conversations/{conversation_id}`

Returns conversation metadata and the full ordered message history. Assistant messages contain `supported` and `sources`; user messages have `supported: null` and no sources. Source items include a note ID and current note title. The initial API has no pagination.

### `POST /v1/conversations/{conversation_id}/messages`

Accepts a non-blank question up to 4,000 characters and returns a complete response in one request. The response includes the persisted user message, assistant message, and updated conversation. Assistant source items contain a note ID and title.

If the project notes do not support an answer, the assistant message has `supported: false` and an empty `sources` array. The answer clearly states that the project notes did not support an answer. The current implementation responds in the question's language; the product language policy remains open, especially when the question and supporting notes use different languages.

Standalone greetings, brief wellbeing exchanges, thanks, farewells, and questions about the assistant's identity or capabilities receive approved local replies. FIKRAH introduces itself as a personal knowledge assistant whose factual answers use the selected project's notes. These exchanges use `supported: null` and `sources: []`: support is not applicable, rather than missing. The existing nullable response field and database column are unchanged. Flutter should show the unsupported-answer state only for `supported == false`, not for `null`.

This is a narrow whole-message policy, currently recognizing common Arabic and English phrases, including the Egyptian Arabic examples "عامل ايه" and "انت مين". It is not an unrestricted small-talk model or a supported-language allowlist. Unknown messages and messages mixing a greeting with a substantive request use the existing grounded workflow. Matching never relies on a greeting prefix, conversation history, note instructions, or a model's self-declared intent. Social exchanges require the same authenticated conversation ownership and request budget, and are persisted normally, but invoke neither embeddings, Qdrant, nor a text model. They therefore do not consume provider quota.

### `DELETE /v1/conversations/{conversation_id}`

Deletes the conversation, all of its messages, and citation records. Deleting a project also deletes its conversations and message history. Deleting a cited note removes its citation records from chat history; the saved message text remains.

### `POST /v1/conversations/{conversation_id}/messages/stream`

An optional streaming version of message submission, with the same JSON request, Bearer authentication, ownership checks, and per-user request budget as `/messages`. The original single-response endpoint remains available. Flutter connects directly to this backend route with an HTTP POST and reads UTF-8 Server-Sent Events; the local browser proxy and its cookies are not mobile dependencies.

- `delta`: `{"content":"..."}` contains a provisional text fragment, not raw provider JSON, reasoning, prompts, or source IDs. Append it to one temporary assistant message.
- `complete`: contains the same full `MessageSubmission` object returned by `/messages`. Replace the temporary message with this authoritative persisted result, including its `supported` value and sources. Social replies and unsupported answers can arrive directly in this event without any deltas.
- `error`: `{"status":503,"detail":"..."}` reports a failure after HTTP headers were sent. Discard provisional content and reload conversation history. HTTP 200 alone is not successful completion. Do not automatically retry paid requests or save previews when the stream disconnects or ends without `complete`.

Validation, authentication, missing conversations, ownership, and budget failures before streaming use the existing JSON HTTP error statuses (`422`, `401`, `404`, `429`, `503`). A conflict or failure during generation is an SSE error event because HTTP status can no longer change. Streaming is not resumable and has no automatic provider replay.

The provider sends structured output with support status and citations before answer text. The decoder releases text only when all header IDs belong to the authorized retrieval snapshot and the model marks the answer supported. Unknown IDs, unsupported answers, and malformed headers never release provisional text. This is a scope/citation gate, not an independent factual verifier; the model must still follow the grounding policy. PostgreSQL revisions and conversation concurrency are rechecked before persistence as with the non-streaming endpoint. Notes can change during a stream, so previews are explicitly provisional: a final unsupported answer replaces them if evidence becomes stale. A bounded queue applies backpressure, disconnect cancellation stops the producer when its next chunk arrives, and provider clients/streams are closed. A blocked upstream read can last until the existing provider timeout.

OpenRouter, native DeepSeek, and Gemini adapters implement streaming transport; prompt policy and structured decoding remain outside the providers. `scripts/test_api_endpoints.py` displays streamed text and requires a completion event instead of treating HTTP 200 alone as success.

OpenRouter chat streaming requests disable optional reasoning by default (`OPENROUTER_CHAT_REASONING_ENABLED=false`) to reduce first-text latency and reasoning-token charges. Set it to `true` for a model that requires reasoning or when it is explicitly needed. This transport setting does not change non-streaming generation, note analysis, native DeepSeek, or Gemini. Local timing showed a supported small synthetic request starting JSON chunks in about 1.5 seconds with reasoning disabled; total latency still varies by model, retrieval, and provider load. See [OpenRouter reasoning controls](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).

## Conversation style and quota

Greetings such as "مساء الورد" receive a greeting only. Routine social replies do not mention or enumerate notes. Identity replies describe FIKRAH briefly; capabilities are described when asked. For factual questions the model answers only from supplied project notes. Unsupported requests receive a brief refusal without listing available note titles, topics, or content.

Requested overviews receive at most three current notes per batch, rather than relying only on a prompt limit. Recognized follow-ups such as "كمل" or "continue" reuse the original overview's retrieval query and exclude note IDs already cited since that overview, using persisted citations rather than trusting text in history. The model still decides whether the requested content is supported. New statements must come from the current batch. This is progressive presentation within the existing eight-candidate retrieval window and 12-message history, not a cursor/pagination API or a promise to enumerate every project note. Arbitrary synonym requests still use the general concise-answer prompt; overview/continuation recognition itself is deliberately bounded.

The user chose model judgment after retrieval over a strict local keyword filter, preserving semantic understanding. Consequently an unsupported substantive question can still consume an embedding and text-generation request while its scope is assessed. Social replies consume no provider quota. Both message routes retain the existing per-user budget (10 requests/minute by default), including social exchanges, so switching transport or adding a greeting cannot bypass request limits. There is no separate paid intent-classification call.

Streaming references: [FastAPI streaming responses](https://fastapi.tiangolo.com/advanced/stream-data/), [OpenRouter streaming](https://openrouter.ai/docs/api/reference/streaming), [Google SDK streaming](https://github.com/googleapis/python-genai), and [Pydantic partial JSON parsing](https://docs.pydantic.dev/latest/concepts/json/#partial-json-parsing).

## Retrieval and privacy

- The backend embeds the question with the configured Gemini embedding model and queries Qdrant within the selected user and project.
- Up to eight semantically relevant note vectors are considered. PostgreSQL must confirm that each note belongs to the authenticated user and selected project, is completed, and has the same revision as the indexed vector before the note content is sent to the text model.
- The text model receives the current question, the latest 12 conversation messages, and authorized note content. Conversation history can clarify a follow-up question but is not evidence for factual claims.
- Returned source IDs are checked against the authorized retrieval set. Invalid or out-of-scope IDs are never returned as citations.
- The configured OpenRouter, DeepSeek, or Gemini text adapter generates the answer. The response is returned as one complete message, without token streaming.
- LLM or retrieval service failures return `503`; the question and assistant response are persisted together only after a complete LLM response is available.

## Persistence

`chat_conversations`, `chat_messages`, and `chat_message_sources` store ownership, conversation history, support status, and citations. Cascading foreign keys and explicit deletion paths remove conversations with their project and remove citations when a note is deleted. Migration `20261109_05` creates the tables and indexes.

HTTP request and response models stay in `app/schemas/chat.py`. Routes map validated requests to framework-independent commands in `app/contracts/chat.py`, and map application results back to the API response models. `ChatService` does not depend on Pydantic schemas.

## Verification

Tests cover conversation persistence, automatic titles, project/user isolation, authorized citations, unsupported answers, and citation/conversation cleanup on deletion.

Before saving an answer, every retrieved citation is rechecked against current note state/revision. If any cited note changed or disappeared during generation, the entire generated answer is discarded, even when another citation remains valid. The service produces an unsupported response instead of retaining claims from invalidated evidence.

Every successful message transaction advances the conversation activity timestamp, including conversations with a user-provided title. Conversation lists therefore reflect the latest successful exchange.
