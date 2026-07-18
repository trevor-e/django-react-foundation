#!/bin/sh
# Canonical PROCESS-role entrypoint for the blueprint's deploy shape (§11, §11a).
# Copy into backend/, keep in sync with the blueprint when flags change.
# Every flag below was earned the hard way — see §11a before "simplifying".
set -e

# Fail-closed config checks gate every role: granian and celery never run Django
# system checks, so a misconfigured production process must not boot (§11b's
# healthcheck catches a broken web boot, but worker/beat have no healthcheck).
python manage.py check --fail-level ERROR

# One image, three roles. Migrations run via the platform's pre-deploy command —
# gated there to the web role so concurrent service deploys can't race migrate —
# never here on boot.
case "$PROCESS" in
  web)
    python manage.py collectstatic --noinput
    # --interface asginl : ASGI without lifespan (Django doesn't implement it)
    # no --blocking-threads : WSGI-only knob; granian hard-errors on ASGI (>1)
    # --workers-kill-timeout: open SSE streams never finish; without this every
    #                         graceful stop wedges on the first connected client
    # --respawn-failed-workers: with 1 worker, a crashed worker otherwise leaves a
    #                           live container serving nothing
    exec granian --interface asginl config.asgi:application \
      --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 \
      --workers-kill-timeout 5 --respawn-failed-workers
    ;;
  worker)
    # --concurrency: prefork defaults to the HOST's core count (32 on Railway),
    # each child with its own eager DB pool (§1b) — always cap it.
    exec celery -A config worker --loglevel info --concurrency "${CELERY_CONCURRENCY:-2}"
    ;;
  beat)
    exec celery -A config beat --loglevel info
    ;;
  *)
    echo "Unknown PROCESS role: '$PROCESS' (expected web|worker|beat)" >&2
    exit 1
    ;;
esac
