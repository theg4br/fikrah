"""Favorite metadata uses explicit state, owned SQL filtering and no LLM work."""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.infrastructure.container import note_service, request_budgets, search_service
from app.models import Note, NoteProcessingJob, Project
from tests import test_notes_api

note_api = test_notes_api.note_api


@pytest.mark.asyncio
async def test_favorite_set_unset_retry_and_filters_preserve_content_state(note_api, monkeypatch):
    client, sessions, owner_id, _, project_id, _, token, _ = note_api
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "favorite-capture"}
    create_body = {"content": "A saved idea", "title": "My idea"}
    created = await client.post(f"/v1/projects/{project_id}/notes", headers=headers, json=create_body)
    original = created.json()
    assert created.status_code == 201
    assert original["is_favorite"] is False
    note_id = original["id"]
    async with sessions() as db:
        db.add(Note(user_id=owner_id, project_id=project_id, content="Unstarred idea"))
        second_project = Project(user_id=owner_id, name="Another project")
        db.add(second_project)
        await db.flush()
        db.add(Note(user_id=owner_id, project_id=second_project.id, content="Elsewhere", is_favorite=True))
        await db.commit()
    notify = AsyncMock()
    budget = AsyncMock()
    monkeypatch.setattr(note_service, "_notify_processing", notify)
    monkeypatch.setattr(request_budgets._store, "consume", budget)
    for _ in range(2):
        response = await client.put(f"/v1/notes/{note_id}/favorite", headers=headers, json={"is_favorite": True})
        assert response.status_code == 200
        assert response.json() == {**original, "is_favorite": True}
    assert (await client.get(f"/v1/notes/{note_id}", headers=headers)).json()["is_favorite"] is True
    all_notes = await client.get(f"/v1/projects/{project_id}/notes", headers=headers)
    assert len(all_notes.json()) == 2
    starred = await client.get(f"/v1/projects/{project_id}/notes?is_favorite=true", headers=headers)
    assert [n["id"] for n in starred.json()] == [note_id]
    unstarred = await client.get(f"/v1/projects/{project_id}/notes?is_favorite=false", headers=headers)
    assert len(unstarred.json()) == 1
    assert unstarred.json()[0]["id"] != note_id
    assert (await client.get(f"/v1/projects/{project_id}/notes?is_favorite=invalid", headers=headers)).status_code == 422
    for _ in range(2):
        response = await client.put(f"/v1/notes/{note_id}/favorite", headers=headers, json={"is_favorite": False})
        assert response.json() == original
    assert (await client.get(f"/v1/projects/{project_id}/notes?is_favorite=true", headers=headers)).json() == []
    notify.assert_not_awaited()
    budget.assert_not_awaited()
    replay = await client.post(f"/v1/projects/{project_id}/notes", headers=headers, json=create_body)
    assert replay.status_code == 201
    assert replay.json()["id"] == note_id
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(NoteProcessingJob)) == 1


@pytest.mark.asyncio
async def test_favorite_access_validation_and_note_deletion(note_api):
    client, _, _, _, project_id, _, token, foreign_token = note_api
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "favorite-private"}
    response = await client.post(f"/v1/projects/{project_id}/notes", headers=headers, json={"content": "Private"})
    note_id = response.json()["id"]
    url = f"/v1/notes/{note_id}/favorite"
    foreign = {"Authorization": f"Bearer {foreign_token}"}
    assert (await client.put(url, json={"is_favorite": True})).status_code == 401
    assert (await client.put(url, headers=foreign, json={"is_favorite": True})).status_code == 404
    assert (await client.get(f"/v1/projects/{project_id}/notes?is_favorite=true", headers=foreign)).status_code == 404
    assert (await client.put(f"/v1/notes/{uuid4()}/favorite", headers=headers, json={"is_favorite": True})).status_code == 404
    for invalid in ({}, {"is_favorite": None}, {"is_favorite": "true"}, {"is_favorite": 1}):
        assert (await client.put(url, headers=headers, json=invalid)).status_code == 422
    assert (await client.put(url, headers=headers, json={"is_favorite": True})).status_code == 200
    assert (await client.delete(f"/v1/notes/{note_id}", headers=headers)).status_code == 204
    assert (await client.put(url, headers=headers, json={"is_favorite": True})).status_code == 404
    assert (await client.get(f"/v1/projects/{project_id}/notes?is_favorite=true", headers=headers)).json() == []


@pytest.mark.asyncio
async def test_favorite_survives_content_edit_and_idempotent_create_replay(note_api):
    client, _, _, _, project_id, _, token, _ = note_api
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "favorite-edit"}
    body = {"content": "Original"}
    created = await client.post(f"/v1/projects/{project_id}/notes", headers=headers, json=body)
    note_id = created.json()["id"]
    await client.put(f"/v1/notes/{note_id}/favorite", headers=headers, json={"is_favorite": True})
    changed = await client.patch(f"/v1/notes/{note_id}", headers=headers, json={"content": "Changed"})
    assert changed.json()["is_favorite"] is True
    assert changed.json()["revision"] == 2
    replay = await client.post(f"/v1/projects/{project_id}/notes", headers=headers, json=body)
    assert replay.json()["is_favorite"] is True
    assert replay.json()["content"] == "Changed"
    assert (await client.delete(f"/v1/projects/{project_id}", headers=headers)).status_code == 204
    assert (await client.get(f"/v1/notes/{note_id}", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_search_reads_favorite_from_postgres_without_reindexing(note_api, monkeypatch):
    client, sessions, _, _, project_id, _, token, _ = note_api
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "favorite-search"}
    created = await client.post(f"/v1/projects/{project_id}/notes", headers=headers, json={"content": "Searchable idea"})
    note_id = UUID(created.json()["id"])
    async with sessions() as db:
        note = await db.get(Note, note_id)
        note.processing_status = "completed"
        await db.commit()

    class Embeddings:
        def embed_query(self, _query):
            return [0.1]

        def close(self):
            pass

    monkeypatch.setattr(search_service, "_embedding_model_factory", Embeddings)
    monkeypatch.setattr(search_service, "_vector_search", lambda *_: [(note_id, 1, 0.9)])
    for favorite in (False, True, False):
        await client.put(f"/v1/notes/{note_id}/favorite", headers=headers, json={"is_favorite": favorite})
        result = await client.post(f"/v1/projects/{project_id}/search", headers=headers, json={"query": "idea"})
        assert result.status_code == 200
        assert result.json()[0]["is_favorite"] is favorite
        assert result.json()[0]["note_id"] == str(note_id)
