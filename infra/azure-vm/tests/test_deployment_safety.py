"""Offline tests: dummy credentials only; never starts containers or Azure resources."""

import argparse
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, RUNTIME / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


config = load("configure_runtime")
params = load("prepare_parameters")


@pytest.fixture
def runtime(tmp_path):
    source = tmp_path / "source.env"
    source.write_text(config.dotenv_text({
        "SMTP_HOST": "smtp.example.com", "SMTP_PORT": "587", "SMTP_START_TLS": "true",
        "SMTP_FROM": "sender@example.com", "SMTP_USERNAME": "sender@example.com",
        "SMTP_PASSWORD": "dummy's ${LITERAL_UNSET_NAME} $password\\withslashes", "GEMINI_API_KEY": "dummy-gemini",
        "LLM_TEXT_PROVIDER": "gemini", "GOOGLE_CLIENT_ID": "dummy.apps.googleusercontent.com",
    }))
    original = source.read_bytes()
    target = tmp_path / "runtime"
    args = argparse.Namespace(source_env=source, output_dir=target, hostname="api.example.com",
                              email="operator@example.com", otp_only=False, email_action_url="https://app.example.com/auth")
    config.generate(args)
    assert source.read_bytes() == original
    return args, target


def test_runtime_credentials_are_private_and_isolated(runtime):
    args, target = runtime
    assert target.stat().st_mode & 0o777 == 0o700
    for file in target.rglob("*"):
        if file.is_file():
            assert file.stat().st_mode & 0o777 == 0o600
    app = dict(line.split("=", 1) for line in (target / "app.env").read_text().splitlines())
    assert app["SMTP_PASSWORD"] == "dummy's ${LITERAL_UNSET_NAME} $password\\withslashes"
    assert "fikrah_app:" in app["DATABASE_URL"]
    assert "fikrah_admin" not in app["DATABASE_URL"]
    assert app["QDRANT_API_KEY"] == dict(line.split("=", 1) for line in (target / "qdrant.env").read_text().splitlines())["QDRANT__SERVICE__API_KEY"]
    assert (target / "secrets/app_db_password").read_text() != (target / "secrets/postgres_admin_password").read_text()
    with pytest.raises(ValueError, match="overwrite"):
        config.generate(args)


def test_no_credentials_in_git(runtime, monkeypatch, tmp_path):
    args, _ = runtime
    monkeypatch.setattr(config, "REPO", tmp_path)
    (tmp_path / ".env.example").write_text(args.source_env.read_text())
    args.output_dir = tmp_path / "tracked-secrets"
    with pytest.raises(ValueError, match="outside Git"):
        config.generate(args)
    assert not args.output_dir.exists()


@pytest.mark.parametrize("value", ["0.0.0.0/0", "0.0.0.0/32", "10.0.0.1/32", "127.0.0.1/32", "8.8.8.8/24", "8.8.8.8", "::1/128"])
def test_ssh_refuses_broad_private_or_invalid_sources(value):
    with pytest.raises(ValueError):
        params.caller_cidr(value)


def test_ssh_accepts_one_public_address():
    assert params.caller_cidr("8.8.8.8/32") == "8.8.8.8/32"


def test_production_compose_boundaries_and_quoting(runtime):
    _, target = runtime
    environment = {**os.environ, "FIKRAH_CONFIG_DIR": str(target), "FIKRAH_IMAGE": "fikrah:dummy",
                   "FIKRAH_HOSTNAME": "api.example.com", "ACME_EMAIL": "operator@example.com"}
    result = subprocess.run(["docker", "compose", "--project-name", "fikrah-offline-check",
                             "--profile", "operations", "--file", str(RUNTIME / "compose.yaml"),
                             "config", "--format", "json"], env=environment, check=True,
                            capture_output=True, text=True)
    document = json.loads(result.stdout)
    services = document["services"]
    assert {name for name, service in services.items() if service.get("ports")} == {"proxy"}
    assert document["networks"]["data"]["internal"] is True
    assert services["api"]["environment"]["SMTP_PASSWORD"].replace("$$", "$") == "dummy's ${LITERAL_UNSET_NAME} $password\\withslashes"
    assert "172.28.0.2" in services["api"]["command"]
    assert "-h 127.0.0.1" in services["postgres"]["healthcheck"]["test"][1]
    assert "--pidfile=/tmp/celerybeat.pid" in services["scheduler"]["command"]
    for name in ("scheduler", "dispatcher-worker", "email-worker", "vector-worker"):
        assert int(services[name]["mem_limit"]) >= 256 * 1024 * 1024
    assert sum(int(service["mem_limit"]) for name, service in services.items() if name != "migrate") <= 3360 * 1024 * 1024
    for name in ("api", "worker", "dispatcher-worker", "email-worker", "vector-worker", "scheduler"):
        assert services[name]["read_only"] is True
        assert services[name]["cap_drop"] == ["ALL"]
    for name in ("postgres", "redis", "qdrant", "scheduler", "vector-worker", "dispatcher-worker"):
        assert set(services[name]["networks"]) == {"data"}


def test_package_runtime_allowlist_excludes_test_directories():
    source = (RUNTIME / "package_release.sh").read_text()
    assert "git archive HEAD infra/azure-vm |" not in source
    assert "runtime_files=(compose.yaml Caddyfile postgres-init.sh bootstrap.sh install_runtime.sh install_release.sh)" in source


def test_otp_only_leaves_legacy_links_unconfigured(runtime, tmp_path):
    args, _ = runtime
    args.output_dir = tmp_path / "otp-runtime"
    args.otp_only = True
    args.email_action_url = None
    config.generate(args)
    content = (args.output_dir / "app.env").read_text()
    assert "EMAIL_ACTION_BASE_URL=" not in content


def test_installer_keeps_postgres_init_accessible_after_root_owned_install():
    source = (RUNTIME / "install_release.sh").read_text()
    private_scripts = source.index("-name '*.sh' -exec chmod 0750")
    public_init = source.index('chmod 0755 "$release_dir/postgres-init.sh"')
    assert public_init > private_scripts


def test_proxy_validation_does_not_reuse_the_live_static_address():
    source = (RUNTIME / "install_release.sh").read_text()
    assert 'run --rm --no-deps -T proxy' not in source
    assert "'docker', 'run', '--rm', '--network', 'none'" in source
    assert '["services"]["proxy"]["image"]' in source
