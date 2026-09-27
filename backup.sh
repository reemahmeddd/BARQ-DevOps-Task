#!/usr/bin/env bash
# Back up the lab PostgreSQL database to backups/<db>-<UTC timestamp>.dump and verify the file is restorable.
# Usage: ./backup.sh [output-directory]      (env: POSTGRES_CONTAINER, default postgres)
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: do not rewrite container paths

container="${POSTGRES_CONTAINER:-postgres}"
out_dir="${1:-backups}"

if [ "$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null)" != "healthy" ]; then
    echo "ERROR: container '$container' is not running and healthy" >&2
    exit 1
fi

# Read the user and database from the container itself, so no password is needed on the host.
db_user="$(docker exec "$container" printenv POSTGRES_USER)"
db_name="$(docker exec "$container" printenv POSTGRES_DB)"

mkdir -p "$out_dir"
file="$out_dir/${db_name}-$(date -u +%Y%m%dT%H%M%SZ).dump"
tmp="$file.partial"
trap 'rm -f "$tmp"' EXIT

echo "Backing up database '$db_name' from container '$container'..."
# -Fc = custom format: compressed, and pg_restore can restore it selectively and with --clean.
docker exec "$container" pg_dump -U "$db_user" -d "$db_name" -Fc > "$tmp"

if [ ! -s "$tmp" ]; then
    echo "ERROR: backup file is empty" >&2
    exit 1
fi
# A backup is only useful if it can be read back: list its contents with pg_restore.
entries="$(docker exec -i "$container" pg_restore --list < "$tmp" | grep -c ' TABLE DATA ' || true)"
if [ "$entries" -lt 1 ]; then
    echo "ERROR: pg_restore cannot read the backup (no table data found)" >&2
    exit 1
fi

mv "$tmp" "$file"
trap - EXIT
sha256sum "$file" > "$file.sha256"
echo "OK: $file ($(wc -c < "$file") bytes, $entries table(s) with data)"
echo "Checksum: $(cut -d' ' -f1 "$file.sha256")"
