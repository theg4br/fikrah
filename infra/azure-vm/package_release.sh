#!/usr/bin/env bash
# Build locally from tracked Git content, never from a directory containing .env.
set -euo pipefail
[[ $# == 1 ]] || { echo 'Usage: package_release.sh PRIVATE_OUTPUT_PARENT' >&2; exit 1; }
repo_dir=$(git rev-parse --show-toplevel)
cd "$repo_dir"
git diff --quiet && git diff --cached --quiet || { echo 'Commit tracked changes before packaging' >&2; exit 1; }
revision=$(git rev-parse HEAD)
output_parent=$(realpath -m -- "$1")
case "$output_parent/" in
  "$repo_dir/"*) [[ $output_parent == "$repo_dir/.copilot-azure/"* ]] || { echo 'Use ignored deployment state or a directory outside Git' >&2; exit 1; } ;;
esac
umask 077
release_dir="$output_parent/$revision"
mkdir -p "$output_parent"
mkdir "$release_dir"
image="fikrah:$revision"
build_context=$(mktemp -d)
trap 'rm -rf -- "$build_context"' EXIT
git archive HEAD Dockerfile .dockerignore requirements.txt app migrations static alembic.ini | tar -x -C "$build_context"
# Resolve the current upstream base once and record its immutable digest. The
# exported application image, rather than a later dependency reinstall, is deployed.
docker pull --platform linux/amd64 python:3.12-slim
base_ref=$(docker image inspect --format '{{index .RepoDigests 0}}' python:3.12-slim)
python3 - "$build_context/Dockerfile" "$base_ref" <<'PIN'
import pathlib, re, sys
file = pathlib.Path(sys.argv[1])
assert re.fullmatch(r'python@sha256:[0-9a-f]{64}', sys.argv[2]), 'Unexpected base digest'
source = file.read_text()
assert source.startswith('FROM python:3.12-slim\n'), 'Review changed Dockerfile base first'
file.write_text(source.replace('FROM python:3.12-slim\n', 'FROM ' + sys.argv[2] + '\n', 1))
PIN
cp "$build_context/Dockerfile" "$release_dir/Dockerfile.pinned"
docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$revision" --tag "$image" "$build_context"
docker image save "$image" | gzip >"$release_dir/image.tar.gz"
runtime_files=(compose.yaml Caddyfile postgres-init.sh bootstrap.sh install_runtime.sh install_release.sh)
runtime_paths=()
for file in "${runtime_files[@]}"; do runtime_paths+=("infra/azure-vm/$file"); done
git archive HEAD "${runtime_paths[@]}" | tar -x -C "$release_dir" --strip-components=2
printf "FIKRAH_IMAGE='%s'\n" "$image" >"$release_dir/release.env"
image_id=$(docker image inspect --format '{{.Id}}' "$image")
python3 - "$release_dir" "$revision" "$image" "$image_id" "$base_ref" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
files = {}
for file in sorted(root.iterdir()):
    if file.is_file():
        with file.open('rb') as source:
            files[file.name] = hashlib.file_digest(source, 'sha256').hexdigest()
(root / 'release.json').write_text(json.dumps({
    'revision': sys.argv[2], 'image': sys.argv[3], 'image_id': sys.argv[4],
    'architecture': 'amd64', 'base_image': sys.argv[5], 'files': files,
}, indent=2) + '\n')
PY
echo "Packaged immutable release $revision. Bundle contains no runtime credentials."
