#!/bin/sh
# Container entrypoint. For the web service (RUN_MIGRATIONS=true) it applies
# database migrations FIRST and refuses to start the server if they fail —
# a half-migrated database must never be served. Other services (the
# scheduler) just verify the schema is current and wait/fail if it isn't.
set -eu

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "[entrypoint] applying database migrations (flask db upgrade)..."
  if ! flask db upgrade; then
    echo "[entrypoint] FATAL: database migration FAILED — not starting. See the error above." >&2
    exit 1
  fi
  echo "[entrypoint] migrations applied."
else
  echo "[entrypoint] verifying database schema is current..."
  tries=0
  until flask schema-status >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -ge "${SCHEMA_WAIT_TRIES:-30}" ]; then
      echo "[entrypoint] FATAL: database schema is not up to date after waiting. Run migrations on the web service." >&2
      flask schema-status >&2 || true
      exit 1
    fi
    echo "[entrypoint] schema not current yet, waiting for the web service to migrate ($tries)..."
    sleep 2
  done
fi

exec "$@"
