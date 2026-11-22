"""Render worker commands without resolving or printing local credentials."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.workers.routing import (
    DISPATCH_QUEUE,
    EMAIL_QUEUE,
    LEGACY_QUEUE,
    NOTES_QUEUE,
    VECTORS_QUEUE,
)


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI required")
def test_compose_workers_have_disjoint_role_queues_and_explicit_capacity(tmp_path):
    compose = Path(__file__).resolve().parents[1] / "compose.yaml"
    env_file = tmp_path / ".env"
    env_file.write_text("FIKRAH_NOTE_WORKER_CONCURRENCY=6\n")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(tmp_path),
            "--env-file",
            str(env_file),
            "-f",
            str(compose),
            "config",
            "--format",
            "json",
            "--no-env-resolution",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    services = json.loads(result.stdout)["services"]
    expected = {
        "worker": (f"{NOTES_QUEUE},{LEGACY_QUEUE}", 6),
        "dispatcher-worker": (DISPATCH_QUEUE, 1),
        "email-worker": (EMAIL_QUEUE, 1),
        "vector-worker": (VECTORS_QUEUE, 1),
    }
    for name, (queues, capacity) in expected.items():
        service = services[name]
        assert service["command"] == [
            "celery",
            "-A",
            "app.workers.celery_app:celery_app",
            "worker",
            "--loglevel=info",
            "--pool=prefork",
            f"--queues={queues}",
            f"--concurrency={capacity}",
        ]
        assert service["environment"]["REDIS_URL"] == "redis://redis:6379/0"
        assert (
            service["depends_on"]["migrate"]["condition"]
            == "service_completed_successfully"
        )
        assert service["depends_on"]["redis"]["condition"] == "service_healthy"
    assert "mailpit" not in services["dispatcher-worker"]["depends_on"]
    assert "qdrant" not in services["dispatcher-worker"]["depends_on"]
    assert "qdrant" not in services["email-worker"]["depends_on"]
    assert "mailpit" not in services["vector-worker"]["depends_on"]
    assert "qdrant" not in services["scheduler"]["depends_on"]
    assert services["worker"]["stop_grace_period"] == "5m0s"
