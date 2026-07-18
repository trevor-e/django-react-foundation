#!/bin/sh
# Runs the whole local dev stack from one command (`make dev`): brings up Postgres/Redis,
# migrates, then runs the Django dev server, a Celery worker, and the Vite dev server
# concurrently, each labeled in the combined output. Ctrl-C stops all of them.
#
# Each server writes straight to its own log file (not through a display pipe) so `$!`
# captures its real PID for cleanup — piping a backgrounded command through `awk` for
# prefixing instead would make `$!` the PID of the last stage (awk), and killing that
# leaves the actual server running as an orphan. Each subshell also `exec`s into its
# command instead of just running it, so the subshell process *becomes* the server
# (no extra non-signal-forwarding layer sitting between `$!` and the real process).
# The frontend runs `vite` directly rather than `pnpm run dev`: pnpm doesn't reliably
# forward SIGINT/SIGTERM to the script it launches, which would otherwise leave vite
# running as an orphan after Ctrl-C.
set -e

cd "$(dirname "$0")/.."

(cd backend && docker compose -f docker-compose.dev.yml up -d)
(cd backend && uv run python manage.py migrate)

# Logs persist in-repo (gitignored) so coding agents can read exactly what each
# server printed: .artifacts/logs/{backend,worker,frontend}.log — same location
# scripts/devctl.sh uses (the background/agent-driven way to run this stack).
logdir="$(pwd)/.artifacts/logs"
mkdir -p "$logdir"
pids=""
trap 'kill $pids 2>/dev/null' EXIT INT TERM

(cd backend && exec env PYTHONUNBUFFERED=1 uv run granian --interface asginl config.asgi:application --port 8000 --reload --workers-kill-timeout 3 --reload-ignore-dirs .ruff_cache --reload-ignore-dirs .pytest_cache --reload-ignore-patterns '\.tmp\.') > "$logdir/backend.log" 2>&1 &
pids="$pids $!"

(cd backend && exec env PYTHONUNBUFFERED=1 uv run celery -A config worker --loglevel info) > "$logdir/worker.log" 2>&1 &
pids="$pids $!"

(cd frontend && exec ./node_modules/.bin/vite) > "$logdir/frontend.log" 2>&1 &
pids="$pids $!"

# Display-only: reads back what the servers above just wrote, with a label per line.
# Best-effort cleanup (killing awk just stops the display, not the underlying server).
tail -n +1 -f "$logdir/backend.log" | awk '{ print "[backend]  " $0; fflush() }' &
pids="$pids $!"
tail -n +1 -f "$logdir/worker.log" | awk '{ print "[worker]   " $0; fflush() }' &
pids="$pids $!"
tail -n +1 -f "$logdir/frontend.log" | awk '{ print "[frontend] " $0; fflush() }' &
pids="$pids $!"

wait
