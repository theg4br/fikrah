#!/usr/bin/env bash
# Run on the dedicated VM with a packaged, reviewed release directory.
set -euo pipefail
[[ $EUID == 0 && $# == 1 ]] || { echo 'Usage: sudo install_release.sh STAGED_RELEASE_DIRECTORY' >&2; exit 1; }
[[ -f /etc/fikrah-host-ready && -f /etc/fikrah/app.env ]] || { echo 'Bootstrap host and install runtime credentials first' >&2; exit 1; }
exec 9>/run/lock/fikrah-maintenance.lock
flock -n 9 || { echo 'Another deployment/backup is running' >&2; exit 1; }
stage=$(realpath -- "$1")
revision=$(python3 - "$stage" <<'PY'
import hashlib, json, pathlib, re, sys
root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / 'release.json').read_text())
revision = manifest['revision']
assert re.fullmatch(r'[0-9a-f]{40}', revision), 'Invalid Git revision'
assert manifest['image'] == 'fikrah:' + revision, 'Unexpected image tag'
assert manifest['architecture'] == 'amd64', 'Unexpected architecture'
expected = set(manifest['files']) | {'release.json'}
assert {p.name for p in root.iterdir()} == expected, 'Unexpected/missing release files'
for name, digest in manifest['files'].items():
    file = root / name
    assert file.name == name and file.is_file() and not file.is_symlink(), 'Unsafe release file'
    with file.open('rb') as source:
        assert hashlib.file_digest(source, 'sha256').hexdigest() == digest, 'Release hash mismatch'
print(revision)
PY
)
release_dir="/opt/fikrah/releases/$revision"
[[ ! -e $release_dir ]] || { echo 'This immutable release already exists; inspect current state before retrying' >&2; exit 1; }
install -d -m 0750 "$release_dir"
cp -- "$stage/"* "$release_dir/"
chown -R root:root "$release_dir"
find "$release_dir" -type f -exec chmod 0640 {} +
find "$release_dir" -type f -name '*.sh' -exec chmod 0750 {} +
# The Postgres entrypoint drops to uid 70 before running this NONSECRET file.
chmod 0755 "$release_dir/postgres-init.sh"
compose=(docker compose --project-name fikrah --env-file /etc/fikrah/compose.env --env-file "$release_dir/release.env" --file "$release_dir/compose.yaml")
"${compose[@]}" config --quiet
gzip -dc "$release_dir/image.tar.gz" | docker image load
python3 - "$release_dir" <<'PY'
import json, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / 'release.json').read_text())
image = json.loads(subprocess.check_output(['docker','image','inspect',manifest['image']]))[0]
assert image['Id'] == manifest['image_id'], 'Loaded image ID differs from package'
assert image['Architecture'] == 'amd64', 'Wrong image platform'
assert image['Config']['Labels']['org.opencontainers.image.revision'] == manifest['revision'], 'Wrong source revision'
PY
"${compose[@]}" pull --quiet postgres redis qdrant proxy
# Validates proxy configuration without changing live services.
proxy_image=$("${compose[@]}" config --images proxy)
python3 - "$release_dir" "$proxy_image" <<'PROXY'
import pathlib, shlex, subprocess, sys
root = pathlib.Path(sys.argv[1])
values = dict(shlex.split(line)[0].split('=', 1) for line in pathlib.Path('/etc/fikrah/compose.env').read_text().splitlines() if line.strip())
subprocess.run([
    'docker', 'run', '--rm', '--network', 'none', '--read-only',
    '--tmpfs', '/tmp', '--tmpfs', '/data', '--tmpfs', '/config',
    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
    '--env', 'FIKRAH_HOSTNAME=' + values['FIKRAH_HOSTNAME'],
    '--env', 'ACME_EMAIL=' + values['ACME_EMAIL'],
    '--volume', str(root / 'Caddyfile') + ':/etc/caddy/Caddyfile:ro',
    sys.argv[2], 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile',
], check=True)
PROXY
# On error, do NOT automatically restart old code against a possibly advanced DB.
trap 'echo "Release failed. Writers may remain stopped. Inspect migration state and protected backup receipts before recovery; no automatic DB downgrade was attempted." >&2' ERR
if [[ -L /opt/fikrah/current ]]; then
  previous=$(readlink -f /opt/fikrah/current)
  echo 'Pausing writers and taking an off-VM backup before migration.'
  python3 "$previous/backup.py" --release "$previous" --already-locked --keep-stopped
  ln -sfn "$previous" /opt/fikrah/previous
fi
"${compose[@]}" up -d --wait --wait-timeout 120 postgres redis qdrant
"${compose[@]}" run --rm --no-deps -T migrate
"${compose[@]}" up -d --wait --wait-timeout 180
"${compose[@]}" exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=5)"
# Docker exports are no longer needed after successful verified load; preserve
# release manifests/scripts and the loaded image for rollback.
rm -- "$release_dir/image.tar.gz"
ln -sfn "$release_dir" /opt/fikrah/current.next
mv -Tf /opt/fikrah/current.next /opt/fikrah/current
cat >/etc/systemd/system/fikrah-backup.service <<'EOF'
[Unit]
Description=FIKRAH consistent maintenance backup to private Azure Blob Storage
After=docker.service network-online.target
Requires=docker.service
[Service]
Type=oneshot
User=root
UMask=0077
ExecStart=/usr/bin/python3 /opt/fikrah/current/backup.py
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/fikrah /run/lock
EOF
cat >/etc/systemd/system/fikrah-backup.timer <<'EOF'
[Unit]
Description=Nightly FIKRAH backup maintenance window
[Timer]
OnCalendar=*-*-* 03:30:00 Africa/Cairo
RandomizedDelaySec=300
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
# Deliberately do not enable the daily timer until an initial backup AND a
# documented restore drill pass, and the maintenance window is approved.
echo "Release $revision is locally ready. Verify public HTTPS, actual queues, auth, LLM flows, backup upload and restore before handover."
