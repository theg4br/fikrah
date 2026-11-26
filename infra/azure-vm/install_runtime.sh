#!/usr/bin/env bash
# Install a directory produced by configure_runtime.py over VERIFIED SSH.
set -euo pipefail
[[ $EUID == 0 && $# == 1 ]] || { echo 'Usage: sudo install_runtime.sh CONFIG_DIRECTORY' >&2; exit 1; }
source_dir=$(realpath -- "$1")
[[ ! -e /etc/fikrah/app.env ]] || { echo 'Existing credentials retained; rotation requires a separate procedure' >&2; exit 1; }
for name in app.env qdrant.env compose.env operations.json secrets/postgres_admin_password secrets/app_db_password secrets/redis_password secrets/redis.conf; do
  [[ -f $source_dir/$name && ! -L $source_dir/$name ]] || { echo 'Missing or unsafe runtime configuration file' >&2; exit 1; }
done
install -d -m 0700 /etc/fikrah /etc/fikrah/secrets
for name in app.env qdrant.env compose.env operations.json; do
  install -o root -g root -m 0600 "$source_dir/$name" "/etc/fikrah/$name"
done
# Parent stays root-only on the host; only individual files are mounted. The
# pinned Alpine Postgres image uses uid 70; Redis is explicitly uid 999.
for name in postgres_admin_password app_db_password; do
  install -o 70 -g 70 -m 0400 "$source_dir/secrets/$name" "/etc/fikrah/secrets/$name"
done
for name in redis_password redis.conf; do
  install -o 999 -g 999 -m 0400 "$source_dir/secrets/$name" "/etc/fikrah/secrets/$name"
done
echo 'Runtime files installed; source credentials were not printed or overwritten.'
