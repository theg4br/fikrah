"""Validate local SMTP routing using Compose's own configuration renderer."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI required")
@pytest.mark.parametrize("service", ["api", "worker", "email-worker"])
@pytest.mark.parametrize("external", [False, True])
def test_email_senders_respect_smtp_configuration(tmp_path: Path, service: str, external: bool) -> None:
    compose = Path(__file__).resolve().parents[1] / "compose.yaml"
    env_file = tmp_path / ".env"
    configured = {
        "SMTP_HOST": "smtp.example.test",
        "SMTP_PORT": "587",
        "SMTP_FROM": "FIKRAH <sender@example.test>",
        "SMTP_USERNAME": "sender@example.test",
        "SMTP_PASSWORD": "synthetic-$test#password",
        "SMTP_USE_TLS": "false",
        "SMTP_START_TLS": "true",
    }
    env_file.write_text(
        "\n".join(f"{key}='{value}'" for key, value in configured.items())
        if external else ""
    )
    result = subprocess.run(
        [
            "docker", "compose",
            "--project-directory", str(tmp_path),
            "--env-file", str(env_file),
            "-f", str(compose),
            "config", "--format", "json", "--no-env-resolution",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        env={key: value for key, value in os.environ.items() if not key.startswith("SMTP_")},
    )
    config = json.loads(result.stdout)["services"][service]
    assert {
        # Compose's rendered config escapes literal dollars for safe reuse.
        key: value.replace("$$", "$")
        for key, value in config["environment"].items()
        if key.startswith("SMTP_")
    } == (configured if external else {
        "SMTP_HOST": "mailpit",
        "SMTP_PORT": "1025",
        "SMTP_FROM": "no-reply@fikrah.local",
        "SMTP_USERNAME": "",
        "SMTP_PASSWORD": "",
        "SMTP_USE_TLS": "false",
        "SMTP_START_TLS": "false",
    })
    assert config["depends_on"]["mailpit"]["condition"] == "service_healthy"
