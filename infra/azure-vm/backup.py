#!/usr/bin/env python3
"""Consistent single-VM maintenance backup, uploaded with host managed identity.

Pauses writers while capturing PostgreSQL, Redis and Qdrant. The archive also
contains runtime credentials: treat it as private even though Azure encrypts
storage at rest. Never expose this directory or backup container publicly.
"""

import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from contextlib import ExitStack
from email.utils import formatdate
from pathlib import Path

WRITERS = ("proxy", "api", "worker", "dispatcher-worker", "email-worker", "vector-worker", "scheduler")
QDRANT_SNAPSHOT = """
import json, os, shutil, sys, urllib.parse, urllib.request
base = os.environ['QDRANT_URL'].rstrip('/')
headers = {'api-key': os.environ['QDRANT_API_KEY']}
request = urllib.request.Request(base+'/snapshots?wait=true', headers=headers, method='POST')
with urllib.request.urlopen(request, timeout=300) as response:
    name = json.load(response)['result']['name']
url = base+'/snapshots/'+urllib.parse.quote(name, safe='')
with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300) as response:
    shutil.copyfileobj(response, sys.stdout.buffer, length=1024*1024)
sys.stdout.buffer.flush()
with urllib.request.urlopen(urllib.request.Request(url+'?wait=true', headers=headers, method='DELETE'), timeout=60):
    pass
"""


def compose(release: Path, *arguments: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([
        "docker", "compose", "--project-name", "fikrah",
        "--env-file", "/etc/fikrah/compose.env", "--env-file", str(release / "release.env"),
        "--file", str(release / "compose.yaml"), *arguments,
    ], check=True, **kwargs)


def upload(archive: Path, account: str, container: str, blob: str) -> str:
    if not re.fullmatch(r"[a-z0-9]{3,24}", account) or container != "backups":
        raise ValueError("Unexpected backup account/container")
    # Do not send managed identity requests through a configured HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    query = urllib.parse.urlencode({"api-version": "2018-02-01", "resource": "https://storage.azure.com/"})
    request = urllib.request.Request(
        "http://169.254.169.254/metadata/identity/oauth2/token?" + query,
        headers={"Metadata": "true"},
    )
    with opener.open(request, timeout=10) as response:
        token = json.load(response)["access_token"]
    path = f"/{container}/{urllib.parse.quote(blob, safe='/')}"
    block_ids = []
    digest = hashlib.sha256()
    # Bound memory independently of archive size; uncommitted blocks expire in Azure.
    with archive.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            block_id = base64.b64encode(f"{len(block_ids):08d}".encode()).decode()
            block_ids.append(block_id)
            digest.update(chunk)
            query = urllib.parse.urlencode({"comp": "block", "blockid": block_id})
            blob_request(account, token, path + "?" + query, chunk)
    body = ("<?xml version=\"1.0\" encoding=\"utf-8\"?><BlockList>" +
            "".join(f"<Latest>{block_id}</Latest>" for block_id in block_ids) + "</BlockList>").encode()
    blob_request(account, token, path + "?comp=blocklist", body, {
        "If-None-Match": "*", "Content-Type": "application/xml",
        "x-ms-blob-content-type": "application/gzip",
        "x-ms-meta-sha256": digest.hexdigest(),
    })
    return digest.hexdigest()


def blob_request(account: str, token: str, path: str, body: bytes, headers: dict | None = None) -> None:
    connection = http.client.HTTPSConnection(f"{account}.blob.core.windows.net", timeout=120)
    try:
        connection.request("PUT", path, body=body, headers={
            "Authorization": "Bearer " + token, "x-ms-version": "2023-11-03",
            "x-ms-date": formatdate(usegmt=True),
            **(headers or {}),
        })
        response = connection.getresponse()
        if response.status != 201:
            raise RuntimeError(f"Backup upload failed with HTTP {response.status}")
        response.read()
    finally:
        connection.close()


def admit_capture(destination: Path) -> None:
    # Never accumulate a new archive after an upload failure. An operator must
    # resolve/upload the retained archive before the next attempt.
    for archive in destination.glob("*.tar.gz"):
        if not archive.with_suffix(".receipt.json").is_file():
            raise RuntimeError("An unuploaded backup exists; resolve it before another capture")
    volumes = [f"fikrah_{name}" for name in (
        "postgres_data", "redis_data", "qdrant_data", "qdrant_snapshots",
    )]
    result = subprocess.check_output(["docker", "volume", "inspect", *volumes], text=True)
    paths = [item["Mountpoint"] for item in json.loads(result)]
    usage = subprocess.check_output(["du", "-sb", "--", *paths], text=True)
    source_bytes = sum(int(line.split()[0]) for line in usage.splitlines())
    # Snapshot + staged capture + compressed archive may coexist. This is a
    # conservative admission estimate, not a guarantee against concurrent growth.
    required = source_bytes * 3 + 512 * 1024 * 1024
    if shutil.disk_usage(destination).free < required:
        raise RuntimeError("Insufficient conservative backup headroom; expand storage or resolve retention")


def capture(release: Path, destination: Path, keep_stopped: bool) -> Path:
    admit_capture(destination)
    current = compose(release, "ps", "--services", "--status", "running", capture_output=True, text=True)
    running = [name for name in current.stdout.splitlines() if name in WRITERS]
    identity = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    archive = destination / f"{identity}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="capture-", dir=destination) as temporary:
        stage = Path(temporary)
        try:
            if running:
                compose(release, "stop", "--timeout", "300", *running)
            with (stage / "postgres.dump").open("xb") as output:
                compose(release, "exec", "-T", "postgres", "pg_dump", "-U", "fikrah_admin", "-d", "fikrah", "-Fc", stdout=output)
            with (stage / "postgres-roles.sql").open("xb") as output:
                compose(release, "exec", "-T", "postgres", "pg_dumpall", "-U", "fikrah_admin", "--globals-only", stdout=output)
            # Use a one-off Python process on the PRIVATE data network, not a host port.
            with (stage / "qdrant.snapshot").open("xb") as output:
                compose(release, "run", "--rm", "--no-deps", "-T", "migrate", "python", "-c", QDRANT_SNAPSHOT, stdout=output)
            compose(release, "exec", "-T", "redis", "sh", "-ec", 'REDISCLI_AUTH="$(cat /run/secrets/redis_password)" redis-cli SAVE', stdout=subprocess.DEVNULL)
            with (stage / "redis.rdb").open("xb") as output:
                compose(release, "exec", "-T", "redis", "cat", "/data/dump.rdb", stdout=output)
            shutil.copytree("/etc/fikrah", stage / "runtime", symlinks=False)
            shutil.copy2(release / "release.json", stage / "release.json")
            shutil.copy2(release / "compose.yaml", stage / "compose.yaml")
            shutil.copy2(release / "release.env", stage / "release.env")
            checksums = {}
            for file in sorted(stage.rglob("*")):
                if file.is_file():
                    with file.open("rb") as source:
                        checksums[str(file.relative_to(stage))] = hashlib.file_digest(source, "sha256").hexdigest()
            (stage / "checksums.json").write_text(json.dumps(checksums, indent=2) + "\n")
        finally:
            # A regular scheduled backup restores ONLY previously running writers.
            # Deployment backups deliberately leave them stopped until migration succeeds.
            if running and not keep_stopped:
                compose(release, "up", "-d", "--no-deps", *running)
        try:
            with tarfile.open(archive, "x:gz") as output:
                for file in sorted(stage.iterdir()):
                    output.add(file, arcname=file.name)
        except (OSError, tarfile.TarError):
            archive.unlink(missing_ok=True)  # Incomplete archives are not recoverable backups.
            raise
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=Path("/opt/fikrah/current"))
    parser.add_argument("--keep-stopped", action="store_true")
    parser.add_argument("--already-locked", action="store_true")
    parser.add_argument("--upload-existing", type=Path, help="Retry one retained archive without another capture")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(1, "Root is required for protected runtime files and Docker.\n")
    os.umask(0o077)
    with ExitStack() as resources:
        if not args.already_locked:
            lock = resources.enter_context(Path("/run/lock/fikrah-maintenance.lock").open("a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run_backup(args)


def run_backup(args: argparse.Namespace) -> None:
    destination = Path("/var/lib/fikrah/backups")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    release = args.release.resolve(strict=True)
    try:
        settings = json.loads(Path("/etc/fikrah/operations.json").read_text())
        if args.upload_existing:
            archive = args.upload_existing.resolve(strict=True)
            if archive.parent != destination or not archive.name.endswith(".tar.gz") or not archive.is_file():
                raise ValueError("Retry must reference a retained archive in the protected backup directory")
            if archive.with_suffix(".receipt.json").exists():
                raise ValueError("Archive already has an upload receipt; inspect Azure before retrying")
        else:
            archive = capture(release, destination, args.keep_stopped)
        account = settings["backup_storage_account"]
        digest = upload(archive, account, settings["backup_container"], archive.name)
        receipt = {"file": archive.name, "sha256": digest, "bytes": archive.stat().st_size,
                   "blob": f"https://{account}.blob.core.windows.net/backups/{archive.name}"}
        archive.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        # Retain the newest two VERIFIED uploaded local archives; Azure retains 30 days.
        # with_suffix replaces '.gz', so receipts use '.tar.receipt.json'.
        verified = sorted(destination.glob("*.tar.receipt.json"))
        for old in verified[:-2]:
            record = json.loads(old.read_text())
            name = record.get("file", "")
            if Path(name).name == name and name.endswith(".tar.gz"):
                (destination / name).unlink(missing_ok=True)
                old.unlink()
        print("Backup captured, hashed and uploaded to private Azure Blob Storage.")
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, tarfile.TarError, http.client.HTTPException) as error:
        # Subprocess argv never contain secrets; exception bodies from remote services
        # can still be unsafe, so emit a type and operational instruction only.
        print(f"Backup failed ({type(error).__name__}); inspect protected host logs/state.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
