"""Keep development files outside the Docker context and runtime image."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_copies_only_runtime_inputs():
    copies = [
        line.split()[-2:]
        for line in (ROOT / "Dockerfile").read_text().splitlines()
        if line.startswith("COPY ")
    ]
    assert copies == [
        ["requirements.txt", "./requirements.txt"],
        ["app/", "./app/"],
        ["migrations/", "./migrations/"],
        ["static/", "./static/"],
        ["alembic.ini", "./alembic.ini"],
    ]


def test_build_context_uses_an_explicit_allowlist():
    rules = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert rules[0] == "**"
    assert {rule for rule in rules if rule.startswith("!")} == {
        "!Dockerfile", "!.dockerignore", "!requirements.txt", "!alembic.ini",
        "!app/", "!app/**", "!migrations/", "!migrations/**", "!static/", "!static/**",
    }
    assert {"**/.env", "**/.env.*", "**/AGENTS.md", "**/.local/"} <= set(rules)


def test_built_image_contains_only_runtime_files():
    image = os.getenv("FIKRAH_TEST_DOCKER_IMAGE")
    if not image:
        pytest.skip("Set FIKRAH_TEST_DOCKER_IMAGE to inspect a freshly built image")
    program = """
import json
from pathlib import Path
root = Path('/app')
assert {p.name for p in root.iterdir()} == {
    'app', 'migrations', 'static', 'alembic.ini', 'requirements.txt'
}
assert (root / 'app/main.py').is_file()
assert (root / 'migrations/env.py').is_file()
assert (root / 'static/emails/welcome.html').is_file()
assert (root / 'static/assets/fikrah-icon-light-mode.png').is_file()
for path in root.rglob('*'):
    assert path.name not in {'AGENTS.md', '.local', '.env', '__pycache__'}
    assert not path.name.startswith('.env.')
print(json.dumps({'runtime_boundary': 'ok'}))
"""
    result = subprocess.run(
        ["docker", "run", "--rm", "--network=none", image, "python", "-c", program],
        capture_output=True, text=True, check=True, timeout=30,
    )
    assert json.loads(result.stdout) == {"runtime_boundary": "ok"}
