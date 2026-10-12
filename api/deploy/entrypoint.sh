#!/bin/sh
# FireSim API entrypoint. With an archive index path and R2 credentials, the index is restored
# from its Litestream replica (first boot on a new volume) and replicated while the API runs.
# Without them the API starts directly (run archive on local disk only; see the startup log).
set -eu

API="${FIRESIM_API_CMD:-uvicorn firesim_api.main:app --host 0.0.0.0 --port 8000}"
CONFIG="${LITESTREAM_CONFIG:-/etc/litestream.yml}"

if [ "${FIRESIM_LITESTREAM:-1}" != "0" ] \
   && [ -n "${FIRESIM_ARCHIVE_DB:-}" ] \
   && [ -n "${FIRESIM_ARCHIVE_S3_BUCKET:-}" ] \
   && [ -n "${FIRESIM_ARCHIVE_S3_ENDPOINT:-}" ] \
   && [ -n "${FIRESIM_ARCHIVE_S3_ACCESS_KEY_ID:-}" ] \
   && [ -n "${FIRESIM_ARCHIVE_S3_SECRET_ACCESS_KEY:-}" ] \
   && command -v litestream >/dev/null 2>&1; then
    mkdir -p "$(dirname "$FIRESIM_ARCHIVE_DB")"
    # A config Litestream cannot read must not stop the API: check it first
    if ! litestream databases -config "$CONFIG" >/dev/null 2>&1; then
        echo "entrypoint: WARNING Litestream config unusable; run-archive index on local disk only" >&2
        exec $API
    fi
    # Bounded: an unreachable bucket must not hold the API back
    if ! timeout "${FIRESIM_LITESTREAM_RESTORE_TIMEOUT:-120}" \
            litestream restore -config "$CONFIG" -if-db-not-exists -if-replica-exists "$FIRESIM_ARCHIVE_DB"; then
        echo "entrypoint: WARNING Litestream restore failed; starting with the local index" >&2
    fi
    echo "entrypoint: run-archive index replicated by Litestream"
    exec litestream replicate -config "$CONFIG" -exec "$API"
fi

echo "entrypoint: WARNING Litestream off (needs FIRESIM_ARCHIVE_DB and FIRESIM_ARCHIVE_S3_*); run-archive index on local disk only" >&2
exec $API
