"""Guard the layer boundaries without importing adapters or contacting providers."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def imports(path: Path) -> set[str]:
    result = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


@pytest.mark.parametrize("directory", ["app/services", "app/rag", "app/core"])
def test_application_layers_do_not_import_entry_points_or_adapters(directory):
    forbidden = ("app.api", "app.infrastructure", "app.workers")
    for path in (ROOT / directory).rglob("*.py"):
        assert not {
            name for name in imports(path)
            if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
        }, path


def test_provider_transports_do_not_compose_application_prompts():
    for path in (ROOT / "app/llm/providers").glob("*.py"):
        assert not imports(path) & {"app.llm.prompts", "app.llm.use_cases"}, path


def test_moved_adapters_and_processing_have_one_canonical_location():
    for path in ["embeddings.py", "vector_store.py", "pipeline.py", "ports.py"]:
        assert not (ROOT / "app/rag" / path).exists()
    for path in [
        "app/llm/providers/gemini_embeddings.py",
        "app/infrastructure/vector/qdrant.py",
        "app/services/note_processing_service.py",
        "app/services/processing_ports.py",
    ]:
        assert (ROOT / path).is_file()
