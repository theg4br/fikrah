"""Adapters for providers exposing the OpenAI Chat Completions interface."""

from collections.abc import Iterator

from openai import OpenAI

from app.llm.ports import StructuredResult


class OpenAICompatibleStructuredTextGenerator:
    def __init__(
        self, api_key: str, base_url: str, model: str, provider_name: str
    ) -> None:
        self._model = model
        self._provider_name = provider_name
        # Celery owns note retries. Replaying inside the SDK multiplies both
        # latency and paid calls before the worker can apply its retry budget.
        self._client = OpenAI(
            api_key=api_key, base_url=base_url, timeout=90, max_retries=0
        )

    def close(self) -> None:
        self._client.close()

    def _stream_request_options(self) -> dict:
        return {}

    def generate_structured(
        self,
        *,
        system_instruction: str,
        user_content: str,
        response_model: type[StructuredResult],
    ) -> StructuredResult:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": system_instruction,
                },
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            max_tokens=3000,
        )
        output = response.choices[0].message.content
        if not output or not output.strip():
            raise ValueError(f"{self._provider_name} returned an empty response")
        return response_model.model_validate_json(output)

    def stream_structured(
        self,
        *,
        system_instruction: str,
        user_content: str,
        response_model: type[StructuredResult],
    ) -> Iterator[str]:
        # No automatic replay after a partial response or a client disconnect.
        with self._client.with_options(max_retries=0).chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            max_tokens=3000,
            stream=True,
            **self._stream_request_options(),
        ) as stream:
            for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason not in (None, "stop"):
                    raise ValueError("Provider did not finish the streamed response")
                if choice.delta.content:
                    yield choice.delta.content
