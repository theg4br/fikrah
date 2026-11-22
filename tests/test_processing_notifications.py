"""Post-commit worker notification preserves the durable note lifecycle."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.container import note_service
from app.models import Note, NoteProcessingJob
from app.services.note_ports import ProcessingDispatchUnavailable
from tests import test_notes_api

note_api = test_notes_api.note_api


@pytest.mark.asyncio
async def test_notification_observes_committed_work_and_does_not_repeat_on_replay(
    note_api, monkeypatch
):
    client, sessions, _, _, project_id, _, token, _ = note_api
    observed = []

    async def notified():
        async with sessions() as db:
            jobs = (await db.scalars(select(NoteProcessingJob))).all()
            observed.append([(job.revision, job.processing_generation) for job in jobs])

    monkeypatch.setattr(note_service, "_notify_processing", notified)
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "immediate"}
    path = f"/v1/projects/{project_id}/notes"
    created = await client.post(path, headers=headers, json={"content": "A note"})
    assert created.status_code == 201
    assert observed == [[(1, 1)]]
    replay = await client.post(path, headers=headers, json={"content": "A note"})
    assert replay.status_code == 201
    assert replay.json()["id"] == created.json()["id"]
    assert len(observed) == 1

    note_path = f"/v1/notes/{created.json()['id']}"
    assert (
        await client.patch(note_path, headers=headers, json={"content": "Edited"})
    ).status_code == 200
    assert observed[-1] == [(1, 1), (2, 2)]
    assert len(observed) == 2
    assert (
        await client.post(note_path + "/processing-retries", headers=headers)
    ).status_code == 202
    assert len(observed) == 2  # Already queued; do not schedule another job.


@pytest.mark.asyncio
async def test_failed_notification_keeps_saved_note_and_durable_job(
    note_api, monkeypatch
):
    client, sessions, _, _, project_id, _, token, _ = note_api
    notify = AsyncMock(side_effect=ProcessingDispatchUnavailable)
    monkeypatch.setattr(note_service, "_notify_processing", notify)
    response = await client.post(
        f"/v1/projects/{project_id}/notes",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "broker-outage",
        },
        json={"content": "Keep this even if Redis is down"},
    )
    assert response.status_code == 201
    assert response.json()["processing_status"] == "queued"
    notify.assert_awaited_once()
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Note)) == 1
        assert await db.scalar(select(func.count()).select_from(NoteProcessingJob)) == 1


@pytest.mark.asyncio
async def test_commit_failure_does_not_notify_worker(note_api, monkeypatch):
    client, sessions, _, _, project_id, _, token, _ = note_api
    notify = AsyncMock()
    monkeypatch.setattr(note_service, "_notify_processing", notify)
    monkeypatch.setattr(
        AsyncSession, "commit", AsyncMock(side_effect=RuntimeError("commit failed"))
    )
    with pytest.raises(RuntimeError, match="commit failed"):
        await client.post(
            f"/v1/projects/{project_id}/notes",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": "failed-commit",
            },
            json={"content": "Not committed"},
        )
    notify.assert_not_awaited()
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(NoteProcessingJob)) == 0


@pytest.mark.asyncio
async def test_response_snapshot_is_not_changed_by_an_immediate_worker(
    note_api, monkeypatch
):
    client, sessions, _, _, project_id, _, token, _ = note_api

    async def fast_worker():
        async with sessions.begin() as db:
            note = await db.scalar(select(Note))
            note.processing_status = "completed"

    monkeypatch.setattr(note_service, "_notify_processing", fast_worker)
    response = await client.post(
        f"/v1/projects/{project_id}/notes",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "fast-worker"},
        json={"content": "Quick processing"},
    )
    assert response.status_code == 201
    assert response.json()["processing_status"] == "queued"
    async with sessions() as db:
        assert (await db.scalar(select(Note))).processing_status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    ["processing-retries", "refinement-regenerations", "refinement-acceptance"],
)
async def test_reprocessing_actions_notify_after_commit(note_api, monkeypatch, action):
    client, sessions, _, _, project_id, _, token, _ = note_api
    notify = AsyncMock()
    monkeypatch.setattr(note_service, "_notify_processing", notify)
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "action"}
    response = await client.post(
        f"/v1/projects/{project_id}/notes",
        headers=headers,
        json={"content": "Original"},
    )
    notify.reset_mock()
    async with sessions.begin() as db:
        note = await db.scalar(select(Note))
        note.processing_status = "completed"
        note.refinement_proposal = "Refined"
    body = (
        {} if action == "processing-retries" else {"expected_processing_generation": 1}
    )
    response = await client.post(
        f"/v1/notes/{response.json()['id']}/{action}", headers=headers, json=body
    )
    assert response.status_code == (200 if action == "refinement-acceptance" else 202)
    notify.assert_awaited_once()
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(NoteProcessingJob)) == 2


@pytest.mark.asyncio
async def test_validation_failure_does_not_notify(note_api, monkeypatch):
    client, _, _, _, project_id, _, token, _ = note_api
    notify = AsyncMock()
    monkeypatch.setattr(note_service, "_notify_processing", notify)
    response = await client.post(
        f"/v1/projects/{project_id}/notes",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "invalid"},
        json={"content": " "},
    )
    assert response.status_code == 422
    notify.assert_not_awaited()
