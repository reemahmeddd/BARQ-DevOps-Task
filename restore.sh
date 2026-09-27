#!/usr/bin/env bash
# Restore a backup made by backup.sh into the lab PostgreSQL database. This REPLACES the current data.
# Usage: ./restore.sh backups/<file>.dump    (env: POSTGRES_CONTAINER, default postgres)
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: do not rewrite container paths

container="${POSTGRES_CONTAINER:-postgres}"
file="${1:-}"

if [ -z "$file" ]; then
    echo "Usage: $0 backups/<file>.dump" >&2
    echo "Available backups:" >&2
    ls -1t backups/*.dump >&2 2>/dev/null || echo "  (none)" >&2
    exit 2
fi
if [ ! -s "$file" ]; then
    echo "ERROR: '$file' does not exist or is empty" >&2
    exit 1
fi
if [ -f "$file.sha256" ] && ! sha256sum --check --status "$file.sha256"; then
    echo "ERROR: checksum mismatch for '$file' (file changed or corrupted)" >&2
    exit 1
fi
if [ "$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null)" != "healthy" ]; then
    echo "ERROR: container '$container' is not running and healthy" >&2
    exit 1
fi
if ! docker exec -i "$container" pg_restore --list < "$file" > /dev/null; then
    echo "ERROR: '$file' is not a readable pg_dump custom-format backup" >&2
    exit 1
fi

db_user="$(docker exec "$container" printenv POSTGRES_USER)"
db_name="$(docker exec "$container" printenv POSTGRES_DB)"

echo "Restoring '$file' into database '$db_name' (current data will be replaced)..."
# --clean --if-exists: drop the existing objects first. --single-transaction: all or nothing.
docker exec -i "$container" pg_restore -U "$db_user" -d "$db_name" \
    --clean --if-exists --no-owner --single-transaction < "$file"

count="$(docker exec "$container" psql -U "$db_user" -d "$db_name" -tAc 'SELECT count(*) FROM records')"
echo "OK: restored. records table now has $count row(s)."
