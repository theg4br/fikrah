"""Exercise Alembic's real connection setup with a percent-encoded URL."""

from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text


def test_alembic_preserves_encoded_connection_options(postgres_engine):
    # The disposable schema's options=-csearch_path=... contains an encoded '='.
    assert "%" in postgres_engine.url.render_as_string(hide_password=False)
    command.upgrade(Config("alembic.ini"), "20261121_02")
    assert "projects" in inspect(postgres_engine).get_table_names()


def test_display_name_migration_preserves_existing_accounts(postgres_engine):
    config = Config("alembic.ini")
    command.upgrade(config, "20261126_02")
    user_id = uuid4()
    with postgres_engine.begin() as db:
        db.execute(text("INSERT INTO users (id, email, password_hash) VALUES (:id, :email, :password)"),
                   {"id": user_id, "email": "before-name@example.com", "password": "existing-hash"})
    command.upgrade(config, "20261127_01")
    with postgres_engine.begin() as db:
        row = db.execute(text("SELECT email, password_hash, name FROM users WHERE id = :id"), {"id": user_id}).one()
        assert tuple(row) == ("before-name@example.com", "existing-hash", None)
        db.execute(text("UPDATE users SET name = :name WHERE id = :id"), {"id": user_id, "name": "علاء جابر"})
    command.downgrade(config, "20261126_02")
    assert "name" not in {column["name"] for column in inspect(postgres_engine).get_columns("users")}
    with postgres_engine.connect() as db:
        assert db.execute(text("SELECT email FROM users WHERE id = :id"), {"id": user_id}).scalar_one() == "before-name@example.com"
