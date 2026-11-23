"""Characterize the existing rules before sharing their implementation."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.core.datetimes import ensure_timezone
from app.core.knowledge import (
    normalized_concepts,
    select_related_notes,
    shared_concepts,
)
from app.models import Project
from app.repositories.project_queries import owned_project_query
from app.schemas.notes import NoteCreate, NoteUpdate
from app.services.notes_service import NoteService


def test_concepts_preserve_casefold_matching_and_left_display_spelling():
    left = [" RAG ", "rag", "Straße", " ", "Embeddings"]
    assert normalized_concepts(left) == {"rag", "strasse", "embeddings"}
    assert shared_concepts(left, ["STRASSE", " Rag", "Other"]) == ["RAG", "Straße"]


def test_connection_selection_preserves_strength_ties_and_existing_limit():
    candidates = [SimpleNamespace(id=i, concepts=["rag"]) for i in range(13)]
    candidates[8].concepts.append("search")
    unrelated = SimpleNamespace(id=99, concepts=["other"])
    selected = select_related_notes(["RAG", "search"], [unrelated, *candidates])
    assert [candidate.id for candidate in selected] == [8, 0, 1, 2, 3, 4, 5, 6, 7, 9]
    assert select_related_notes([], candidates) == []


@pytest.mark.parametrize("count", [1_000, 10_000])
def test_large_candidate_sets_keep_the_existing_limit_and_stable_tie_order(count):
    candidates = [SimpleNamespace(id=i, concepts=["RAG", "Search"]) for i in range(count)]
    selected = select_related_notes([" rag ", "SEARCH"], candidates)
    assert [candidate.id for candidate in selected] == list(range(10))


@pytest.mark.parametrize("schema", [NoteCreate, NoteUpdate])
@pytest.mark.parametrize("character", ["a", "💡"])
def test_large_notes_use_character_limits_for_ascii_and_unicode(monkeypatch, schema, character):
    monkeypatch.setenv("FIKRAH_NOTE_MAX_CHARACTERS", "100000")
    content = character * 100_000
    assert schema(content=content).content == content
    with pytest.raises(ValidationError, match="100000"):
        schema(content=content + character)


@pytest.mark.parametrize("aware", [False, True])
def test_timestamp_compatibility_preserves_aware_values(aware):
    value = datetime(2026, 11, 23, tzinfo=timezone(timedelta(hours=2)) if aware else None)
    result = ensure_timezone(value)
    if aware:
        assert result is value
    else:
        assert result == value.replace(tzinfo=timezone.utc)


@pytest.mark.parametrize("lock", [False, True])
def test_owned_project_query_preserves_predicates_and_lock(lock):
    user_id, project_id = uuid4(), uuid4()
    expected = select(Project).where(Project.id == project_id, Project.user_id == user_id)
    if lock:
        expected = expected.with_for_update()
    actual = owned_project_query(user_id, project_id, lock=lock)
    dialect = postgresql.dialect()
    assert str(actual.compile(dialect=dialect)) == str(expected.compile(dialect=dialect))
    assert actual.compile().params == expected.compile().params


@pytest.mark.parametrize("schema", [NoteCreate, NoteUpdate])
@pytest.mark.parametrize("title,expected", [(None, None), ("   ", None), (" Title ", "Title")])
def test_shared_title_normalizer_preserves_null_and_whitespace(schema, title, expected):
    assert schema(content="Note", title=title).title == expected


@pytest.mark.parametrize("schema", [NoteCreate, NoteUpdate])
def test_title_limit_still_runs_before_whitespace_normalization(schema):
    with pytest.raises(ValidationError):
        schema(content="Note", title=" " * 241)


@pytest.mark.parametrize("increment_revision", [False, True])
def test_queue_transition_keeps_revision_and_generation_distinct(increment_revision):
    repository = Mock()
    service = NoteService(repository=repository)
    note = SimpleNamespace(revision=3, processing_generation=7, processing_status="completed")
    service._queue_processing(None, note, increment_revision=increment_revision)
    assert note.revision == (4 if increment_revision else 3)
    assert note.processing_generation == 8
    assert note.processing_status == "queued"
    repository.enqueue_processing.assert_called_once_with(None, note)
