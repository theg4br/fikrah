"""Progressive note overviews use current citations and a stable retrieval query."""

import json

import pytest

from app.infrastructure.container import chat_service, search_service
from app.llm.contracts import ChatAnswer
from app.models import Note
from tests import test_chat_api

chat_api = test_chat_api.chat_api


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_overview_continues_in_batches_without_repeating_sources(
    chat_api, monkeypatch, streaming
):
    client, sessions, owner, project, _, first_note, _, token, _ = chat_api
    async with sessions() as db:
        extra = [
            Note(
                user_id=owner,
                project_id=project,
                title=f"Note {index}",
                content=f"Recorded item {index}",
                revision=2,
                processing_status="completed",
            )
            for index in range(6)
        ]
        db.add_all(extra)
        await db.commit()
        note_ids = [first_note, *(note.id for note in extra)]
    queries, batches = [], []

    class Embeddings(test_chat_api.FakeEmbeddingModel):
        def embed_query(self, query):
            queries.append(query)
            return super().embed_query(query)

    class Generator:
        def close(self):
            pass

        def generate_structured(self, **kwargs):
            notes = json.loads(kwargs["user_content"])["project_notes"]
            ids = [note["note_id"] for note in notes]
            batches.append(ids)
            return ChatAnswer(
                supported=True,
                source_note_ids=ids,
                answer="; ".join(note["content"] for note in notes),
            )

        def stream_structured(self, **kwargs):
            yield self.generate_structured(**kwargs).model_dump_json()

    monkeypatch.setattr(search_service, "_embedding_model_factory", Embeddings)
    monkeypatch.setattr(
        search_service,
        "_vector_search",
        lambda *_: [(note, 2, 0.9) for note in note_ids],
    )
    monkeypatch.setattr(chat_service, "_text_generator_factory", Generator)
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        f"/v1/projects/{project}/conversations", headers=headers, json={}
    )
    path = f"/v1/conversations/{created.json()['id']}/messages" + (
        "/stream" if streaming else ""
    )
    for question in ["لخصلي النوتس المتاحة عندك", "شكرا", "كمل", "كمل"]:
        response = await client.post(path, headers=headers, json={"content": question})
        assert response.status_code == 200
        if streaming:
            assert (
                "event: complete" in response.text
                and "event: error" not in response.text
            )
        else:
            assert response.json()["assistant_message"]["supported"] is (
                None if question == "شكرا" else True
            )
    assert list(map(len, batches)) == [3, 3, 1]
    assert [note for batch in batches for note in batch] == [
        str(note) for note in note_ids
    ]
    assert queries == ["لخصلي النوتس المتاحة عندك"] * 3
