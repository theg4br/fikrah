#!/bin/sh
set -eu
# Runs once on a NEW database volume, under the official Postgres entrypoint.
# Password is generated as hex and sent only on stdin, never in argv or logs.
password=$(cat "$APP_DB_PASSWORD_FILE")
case "$password" in
  ''|*[!a-f0-9]*) echo 'Invalid generated application database password' >&2; exit 1 ;;
esac
{
  printf "CREATE ROLE fikrah_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '%s';\n" "$password"
  printf 'ALTER DATABASE fikrah OWNER TO fikrah_app;\n'
  printf 'REVOKE CREATE ON SCHEMA public FROM PUBLIC;\n'
  printf 'GRANT USAGE, CREATE ON SCHEMA public TO fikrah_app;\n'
} | psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname fikrah >/dev/null
unset password
