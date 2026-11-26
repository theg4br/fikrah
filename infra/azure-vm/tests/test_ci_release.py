"""Release safety and main-only deployment gates without cloud credentials."""

import ast
import hashlib
import importlib.util
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

RUNTIME = Path(__file__).resolve().parents[1]
ROOT = RUNTIME.parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, RUNTIME / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deployer = load("deploy_ci_release")
packager = load("package_ci_release")


def test_main_push_is_required_even_for_manual_runs():
    workflow = (ROOT / ".github/workflows/backend-checks.yml").read_text()
    assert "branches: [dev, main]" in workflow
    deploy_job = workflow.split("\n  deploy:\n", 1)[1]
    assert "needs: backend" in deploy_job
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in deploy_job
    assert "id-token: write" in deploy_job
    assert "name: demo" in deploy_job
    assert "cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}" in workflow
    assert "pull_request_target" not in workflow


@pytest.mark.parametrize("repository,revision,digest", [
    ("someone/else", "a" * 40, "b" * 64),
    ("theg4br/fikrah", "main; id", "b" * 64),
    ("theg4br/fikrah", "a" * 40, "bad"),
])
def test_renderer_refuses_untrusted_release_identifiers(repository, revision, digest):
    with pytest.raises(ValueError):
        deployer.render_script(repository, revision, digest)


def test_guest_script_is_valid_python_and_checks_the_payload():
    script = deployer.render_script("theg4br/fikrah", "a" * 40, "b" * 64)
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    python_source = script.split("<<'FIKRAH_DEPLOY'\n", 1)[1].rsplit("\nFIKRAH_DEPLOY", 1)[0]
    ast.parse(python_source)
    assert "hashlib.file_digest" in python_source
    assert "item.isfile()" in python_source
    assert "extractall(stage,filter='data')" in python_source
    assert "no database rollback attempted" in python_source
    assert "AZURE_CLIENT_ID" not in python_source


def test_guest_exit_zero_without_application_marker_is_failure(tmp_path):
    checksums = tmp_path / "SHA256SUMS"
    checksums.write_text("b" * 64 + "  fikrah-release.tar.gz\n")
    args = SimpleNamespace(checksums=checksums, script=tmp_path / "deploy.sh", repository="theg4br/fikrah",
                           revision="a" * 40, subscription="sub", resource_group="rg", vm="vm")
    response = SimpleNamespace(stdout=json.dumps({"value": [{"message": "Enable succeeded; guest failed"}]}))
    with patch.object(deployer.subprocess, "run", return_value=response), pytest.raises(RuntimeError, match="readiness"):
        deployer.deploy(args)


def test_guest_application_marker_is_required(tmp_path):
    checksums = tmp_path / "SHA256SUMS"
    checksums.write_text("b" * 64 + "  fikrah-release.tar.gz\n")
    args = SimpleNamespace(checksums=checksums, script=tmp_path / "deploy.sh", repository="theg4br/fikrah",
                           revision="a" * 40, subscription="sub", resource_group="rg", vm="vm")
    response = SimpleNamespace(stdout=json.dumps({"value": [{"message": "FIKRAH_DEPLOY_OK " + args.revision}]}))
    with patch.object(deployer.subprocess, "run", return_value=response):
        deployer.deploy(args)


def test_package_rejects_an_image_other_than_the_tested_revision(tmp_path):
    image = {"Config": {"Labels": {"org.opencontainers.image.revision": "b" * 40}}, "Architecture": "amd64"}
    with patch.object(packager.subprocess, "check_output", return_value=json.dumps([image]).encode()), pytest.raises(ValueError, match="revision"):
        packager.package("dummy", "a" * 40, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_package_contains_only_checksummed_runtime_inputs(tmp_path):
    revision = "a" * 40
    inspected = {"Id": "sha256:" + "b" * 64, "Architecture": "amd64",
                 "Config": {"Labels": {"org.opencontainers.image.revision": revision}}}
    def export(image, destination):
        destination.write_bytes(b"dummy-image")
    with patch.object(packager.subprocess, "check_output", return_value=json.dumps([inspected]).encode()), patch.object(
        packager.subprocess, "run"
    ), patch.object(packager, "export_image", side_effect=export):
        archive = packager.package("tested-image", revision, tmp_path / "bundle")
    with tarfile.open(archive) as bundle:
        assert set(bundle.getnames()) == set(packager.RUNTIME_FILES) | {"image.tar.gz", "release.json", "release.env"}
        manifest = json.load(bundle.extractfile("release.json"))
        assert manifest["image_id"] == inspected["Id"]
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(bundle.extractfile(name).read()).hexdigest() == digest
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (archive.parent / "SHA256SUMS").read_text() == digest + "  fikrah-release.tar.gz\n"
