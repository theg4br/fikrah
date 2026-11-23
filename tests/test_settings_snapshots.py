"""Typed immutable snapshots preserve established environment names/defaults."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings, get_worker_settings


def test_all_existing_environment_names_and_default_values_are_preserved():
    old = json.loads((Path(__file__).parent / "fixtures/settings-defaults.json").read_text())
    settings = get_settings({})
    current = {
        str(field.validation_alias): getattr(settings, name)
        for name, field in Settings.model_fields.items()
    }
    assert old.keys() <= current.keys()
    for alias, original in old.items():
        value = current[alias]
        if isinstance(original, str) and isinstance(value, bool):
            value = str(value).lower()
        elif isinstance(original, str) and isinstance(value, int):
            value = str(value)
        assert value == original, alias


def test_unchanged_environment_reuses_immutable_snapshot(monkeypatch):
    monkeypatch.setenv("FIKRAH_NOTE_MAX_CHARACTERS", "1234")
    first = get_settings()
    assert get_settings() is first
    with pytest.raises(ValidationError):
        first.redis_url = "redis://changed.invalid"
    monkeypatch.setenv("FIKRAH_NOTE_MAX_CHARACTERS", "4321")
    changed = get_settings()
    assert changed is not first
    assert changed.fikrah_note_max_characters == 4321
    monkeypatch.setenv("FIKRAH_NOTE_MAX_CHARACTERS", "1234")
    assert get_settings() is first


def test_explicit_mapping_uses_defaults_instead_of_process_values(monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "2468")
    assert get_settings({}).smtp_port == 587
    assert get_settings({"SMTP_PORT": "1025"}).smtp_port == 1025


@pytest.mark.parametrize("value,expected", [
    ("true", True), ("TRUE", True), ("false", False),
    ("1", False), ("yes", False), (" true ", False),
])
def test_smtp_boolean_interpretation_preserves_existing_convention(value, expected):
    assert get_settings({"SMTP_USE_TLS": value}).smtp_use_tls is expected


@pytest.mark.parametrize("value", ["1.0", "0", "-1", "invalid"])
def test_worker_integer_validation_keeps_existing_string_semantics(value):
    with pytest.raises(ValueError, match="FIKRAH_DISPATCH_BATCH_SIZE must be a positive integer"):
        get_worker_settings({"FIKRAH_DISPATCH_BATCH_SIZE": value})
