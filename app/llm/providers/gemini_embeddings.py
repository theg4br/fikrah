"""Gemini embedding transport adapter."""

from google import genai
from google.genai import types

from app.core.execution import check_execution_budget, remaining_time
from app.core.telemetry import timed


class GeminiEmbeddingModel:
    def __init__(self, api_key: str, model: str, dimensions: int) -> None:
        self._model = model
        self._dimensions = dimensions
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=max(1, int(remaining_time(90) * 1000)),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def close(self) -> None:
        self._client.close()

    @timed("embedding.note")
    def embed_note(self, content: str) -> list[float]:
        return self._embed(content, "RETRIEVAL_DOCUMENT")

    @timed("embedding.query")
    def embed_query(self, query: str) -> list[float]:
        return self._embed(query, "RETRIEVAL_QUERY")

    def _embed(self, content: str, task_type: str) -> list[float]:
        check_execution_budget()
        response = self._client.models.embed_content(
            model=self._model,
            contents=content,
            config=types.EmbedContentConfig(
                task_type=task_type, output_dimensionality=self._dimensions
            ),
        )
        check_execution_budget()
        if not response.embeddings or not response.embeddings[0].values:
            raise ValueError("Gemini returned an empty embedding")
        return response.embeddings[0].values
