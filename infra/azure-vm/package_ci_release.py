#!/usr/bin/env python3
"""Package the exact Docker image that passed CI, without runtime credentials."""

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
from pathlib import Path

RUNTIME_FILES = (
    "compose.yaml", "Caddyfile", "postgres-init.sh", "bootstrap.sh",
    "install_runtime.sh", "install_release.sh",
)


def export_image(image: str, destination: Path) -> None:
    """Stream Docker export through gzip with bounded Python memory."""
    with destination.open("xb") as target:
        save = subprocess.Popen(["docker", "image", "save", image], stdout=subprocess.PIPE)
        try:
            compressed = subprocess.run(["gzip", "-1"], stdin=save.stdout, stdout=target, check=False)
            save.stdout.close()
            if save.wait() or compressed.returncode:
                raise RuntimeError("Could not export the tested image")
        finally:
            if save.poll() is None:
                save.kill()
                save.wait()


def package(image: str, revision: str, output: Path) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected a full Git commit SHA")
    inspected = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
    if inspected["Config"]["Labels"].get("org.opencontainers.image.revision") != revision:
        raise ValueError("Image revision does not match the tested commit")
    if inspected["Architecture"] != "amd64":
        raise ValueError("The demo VM requires amd64")
    output.mkdir(parents=True, exist_ok=False)
    stage = output / "release"
    stage.mkdir()
    root = Path(__file__).resolve().parent
    for name in RUNTIME_FILES:
        (stage / name).write_bytes((root / name).read_bytes())
    (stage / "release.env").write_text(f"FIKRAH_IMAGE='fikrah:{revision}'\n")
    subprocess.run(["docker", "image", "tag", image, f"fikrah:{revision}"], check=True)
    export_image(f"fikrah:{revision}", stage / "image.tar.gz")
    files = {}
    for file in stage.iterdir():
        with file.open("rb") as source:
            files[file.name] = hashlib.file_digest(source, "sha256").hexdigest()
    (stage / "release.json").write_text(json.dumps({
        "revision": revision, "image": f"fikrah:{revision}",
        "image_id": inspected["Id"], "architecture": "amd64", "files": files,
    }, indent=2) + "\n")
    archive = output / "fikrah-release.tar.gz"
    with tarfile.open(archive, "x:gz", compresslevel=1) as target:
        for file in sorted(stage.iterdir()):
            target.add(file, arcname=file.name)
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    (output / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(package(args.image, args.revision, args.output))


if __name__ == "__main__":
    main()
