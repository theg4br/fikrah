"""Both install entry points must pin and hash the same runtime dependency graph."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def locked_packages(path: Path) -> dict[str, str]:
    result = {}
    current = None
    hashed = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--hash=sha256:"):
            assert current is not None
            assert re.fullmatch(r"--hash=sha256:[0-9a-f]{64}(?: \\)?", line)
            hashed.add(current)
        else:
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+) \\", line)
            assert match, f"Unpinned or unhashed requirement: {line}"
            current, version = match.groups()
            assert current not in result
            result[current] = version
    assert result
    assert hashed == result.keys()
    return result


def test_runtime_and_development_locks_share_exact_runtime_versions():
    runtime = locked_packages(ROOT / "requirements.txt")
    development = locked_packages(ROOT / "requirements-dev.txt")
    assert runtime.items() <= development.items()
    assert {"pytest", "pytest-asyncio", "ruff", "pip-tools"} <= development.keys()


def test_lock_sources_and_docker_install_entry_point_remain_explicit():
    assert (ROOT / "requirements.in").is_file()
    assert "-r requirements.in" in (ROOT / "requirements-dev.in").read_text()
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "--require-hashes -r requirements.txt" in dockerfile
