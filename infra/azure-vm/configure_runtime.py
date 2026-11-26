#!/usr/bin/env python3
"""Generate NEW server credentials without modifying the source environment file.

Run locally with the project's existing virtualenv. Transfer the result over
verified SSH, then install it as /etc/fikrah with root-only directory access.
"""

import argparse
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
EXCLUDED = {
    "DATABASE_URL", "REDIS_URL", "QDRANT_URL", "QDRANT_API_KEY",
    "FIKRAH_JWT_SECRET", "EMAIL_ACTION_BASE_URL", "POSTGRES_DB",
    "POSTGRES_USER", "POSTGRES_PASSWORD",
}


def https_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https" or not host or parsed.username or parsed.password
        or host in {"localhost", "127.0.0.1", "::1"}
        or host.endswith((".localhost", ".invalid", ".local"))
    ):
        raise ValueError("Supply an approved public HTTPS email-action URL")
    return value


def dotenv_text(values: dict[str, str]) -> str:
    """Single quotes prevent Compose interpolation of dollar signs in secrets."""
    lines = []
    for key, value in sorted(values.items()):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or any(c in value for c in "\r\n\x00"):
            raise ValueError("Runtime environment keys/values must be single-line")
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        lines.append(f"{key}='{escaped}'\n")
    return "".join(lines)


def raw_env_text(values: dict[str, str]) -> str:
    """Compose env_file format=raw preserves provider credentials byte for byte."""
    dotenv_text(values)  # Validate keys and reject multiline/NUL values.
    return "".join(f"{key}={value}\n" for key, value in sorted(values.items()))


def write_private(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        handle.write(value)


def generate(args: argparse.Namespace) -> None:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", args.hostname):
        raise ValueError("Hostname must be a DNS name without scheme, path or port")
    if "." not in args.hostname or args.hostname.endswith((".local", ".invalid")):
        raise ValueError("Supply the actual public hostname")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", args.email):
        raise ValueError("Supply a certificate contact email")
    if not re.fullmatch(r"[a-z0-9]{3,24}", args.backup_storage_account):
        raise ValueError("Invalid backup storage account name")
    if not args.source_env.is_file():
        raise ValueError("Source environment file does not exist")
    action_url = None if args.otp_only else https_url(args.email_action_url)
    # Read known project options only. New deployment never copies local users/data.
    defaults = dotenv_values(REPO / ".env.example", interpolate=False)
    source = dotenv_values(args.source_env, interpolate=False)
    app = {
        key: str(source.get(key, value) or "")
        for key, value in defaults.items()
        if key not in EXCLUDED and not key.startswith("POSTGRES_")
        and not key.endswith(("_PORT", "_CONCURRENCY"))
    }
    for key in ("SMTP_PORT", "SMTP_USE_TLS", "SMTP_START_TLS"):
        app[key] = str(source.get(key, defaults.get(key)) or "")
    if app.get("SMTP_HOST", "").lower() in {"", "localhost", "127.0.0.1", "mailpit"}:
        raise ValueError("Production SMTP must be configured; Mailpit is not deployed")
    if app.get("SMTP_USE_TLS", "").lower() != "true" and app.get("SMTP_START_TLS", "").lower() != "true":
        raise ValueError("Production SMTP must use TLS or STARTTLS")
    if not app.get("SMTP_FROM") or not app.get("SMTP_USERNAME") or not app.get("SMTP_PASSWORD"):
        raise ValueError("SMTP sender, username and password are required")
    if not app.get("GEMINI_API_KEY"):
        raise ValueError("Gemini credentials are needed for embeddings and transcription")
    provider_key = {
        "openrouter": "OPENROUTER_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }.get(app.get("LLM_TEXT_PROVIDER", ""))
    if provider_key is None or not app.get(provider_key):
        raise ValueError("The selected text provider needs credentials")
    if not app.get("GOOGLE_CLIENT_ID"):
        raise ValueError("The approved Google audience/client ID is required")

    target = args.output_dir.resolve()
    if target.is_relative_to(REPO) and not target.is_relative_to(REPO / ".copilot-azure"):
        raise ValueError("Write credentials outside Git or under ignored .copilot-azure")
    # Reject malformed environment values before creating any output files.
    dotenv_text(app)
    if target.exists():
        raise ValueError("Refusing to overwrite existing server credentials")
    target.mkdir(mode=0o700, parents=True)
    os.chmod(target, 0o700)
    secret_dir = target / "secrets"
    secret_dir.mkdir(mode=0o700)
    passwords = {key: secrets.token_hex(32) for key in (
        "postgres_admin_password", "app_db_password", "redis_password", "qdrant_key",
    )}
    app.update({
        "DATABASE_URL": f"postgresql+asyncpg://fikrah_app:{passwords['app_db_password']}@postgres:5432/fikrah",
        "REDIS_URL": f"redis://:{passwords['redis_password']}@redis:6379/0",
        "QDRANT_URL": "http://qdrant:6333",
        "QDRANT_API_KEY": passwords["qdrant_key"],
        "FIKRAH_JWT_SECRET": secrets.token_hex(48),
    })
    if action_url is not None:
        app["EMAIL_ACTION_BASE_URL"] = action_url
    write_private(target / "app.env", raw_env_text(app))
    write_private(target / "qdrant.env", raw_env_text({
        "QDRANT__SERVICE__API_KEY": passwords["qdrant_key"],
        "QDRANT__TELEMETRY_DISABLED": "true",
    }))
    write_private(target / "compose.env", dotenv_text({
        "FIKRAH_HOSTNAME": args.hostname, "ACME_EMAIL": args.email,
        "FIKRAH_CONFIG_DIR": "/etc/fikrah",
    }))
    for key in ("postgres_admin_password", "app_db_password", "redis_password"):
        write_private(secret_dir / key, passwords[key] + "\n")
    write_private(secret_dir / "redis.conf", (
        "bind 0.0.0.0\nprotected-mode yes\nport 6379\ndir /data\n"
        "appendonly yes\nappendfsync everysec\nmaxmemory 128mb\n"
        f"maxmemory-policy noeviction\nrequirepass {passwords['redis_password']}\n"
    ))
    write_private(target / "operations.json", json.dumps({
        "backup_storage_account": args.backup_storage_account,
        "backup_container": "backups",
        "hostname": args.hostname,
    }, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--email", required=True)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--email-action-url")
    actions.add_argument("--otp-only", action="store_true", help="Leave legacy email links unconfigured")
    parser.add_argument("--backup-storage-account", default="stfikrahbackup2310")
    args = parser.parse_args()
    try:
        generate(args)
    except (OSError, ValueError) as error:
        # Never print environment values or SDK configuration exceptions.
        parser.exit(1, f"Configuration refused: {error}\n")
    print("New protected runtime configuration generated. No credentials printed.")


if __name__ == "__main__":
    main()
