#!/bin/sh
# Dev-stack control for coding agents (and humans): devctl.sh up|down|status|logs.
#
# Runs the same stack as `make dev` (Django :8000, Celery worker, Vite :5173) in the
# BACKGROUND: each server's exact console output goes to .artifacts/logs/<name>.log
# and its PID to .artifacts/run/<name>.pid. `make dev` writes to the same log files,
# so "what did the console say?" is always answered by reading .artifacts/logs/,
# regardless of who started the stack.
#
#   up      DB/Redis up + migrate, then start backend/worker/frontend in the
#           background and wait until :8000 and :5173 accept connections.
#           Idempotent — already-running services are left alone.
#   down    Stop the three servers (leaves the Docker DB/Redis containers running).
#   status  One line per service: running/stopped, PID, port reachability.
#   logs    Tail the logs: `logs [name] [lines]` (default: all services, 40 lines).
set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOGDIR="$ROOT/.artifacts/logs"
RUNDIR="$ROOT/.artifacts/run"
mkdir -p "$LOGDIR" "$RUNDIR"

SERVICES="backend worker frontend"

alive() { # alive <name>
  [ -f "$RUNDIR/$1.pid" ] && kill -0 "$(cat "$RUNDIR/$1.pid")" 2>/dev/null
}

start() { # start <name> <subdir> <cmd...>
  name=$1
  dir=$2
  shift 2
  if alive "$name"; then
    echo "[$name] already running (pid $(cat "$RUNDIR/$name.pid"))"
    return 0
  fi
  : >"$LOGDIR/$name.log"
  # exec so the subshell PID *is* the server (see scripts/dev.sh for why).
  (cd "$ROOT/$dir" && exec env PYTHONUNBUFFERED=1 "$@") >"$LOGDIR/$name.log" 2>&1 &
  echo $! >"$RUNDIR/$name.pid"
  echo "[$name] started (pid $!) -> .artifacts/logs/$name.log"
}

wait_http() { # wait_http <name> <url> <timeout-seconds>
  i=0
  while [ "$i" -lt "$3" ]; do
    # Any HTTP response (even 404) means the server is up; only refused/timeout fails.
    if curl -s -o /dev/null --max-time 2 "$2"; then
      echo "[$1] ready at $2"
      return 0
    fi
    if ! alive "$1"; then
      echo "[$1] DIED during startup — last log lines:" >&2
      tail -n 25 "$LOGDIR/$1.log" >&2
      return 1
    fi
    i=$((i + 1))
    sleep 1
  done
  echo "[$1] not reachable at $2 after $3s — last log lines:" >&2
  tail -n 25 "$LOGDIR/$1.log" >&2
  return 1
}

serving() { # serving <url> -> 0 if something already answers there
  curl -s -o /dev/null --max-time 2 "$1"
}

cmd_up() {
  (cd "$ROOT/backend" && docker compose -f docker-compose.dev.yml up -d)
  (cd "$ROOT/backend" && uv run python manage.py migrate)

  # If a server not started by devctl already answers on the port (make dev, the
  # Claude browser preview, a user terminal), leave it alone — starting a duplicate
  # would just crash on port-in-use. Its logs are in .artifacts/logs/ anyway if it
  # came from dev.sh.
  if ! alive backend && serving http://localhost:8000/; then
    echo "[backend] :8000 already served by a process devctl didn't start — leaving it alone"
  else
    start backend backend uv run granian --interface asginl config.asgi:application --port 8000 --reload --workers-kill-timeout 3 --reload-ignore-dirs .ruff_cache --reload-ignore-dirs .pytest_cache --reload-ignore-patterns '\.tmp\.'
    # Only pair a worker with a backend we own; `make dev` runs its own worker.
    start worker backend uv run celery -A config worker --loglevel info
    wait_http backend http://localhost:8000/ 60
  fi

  if ! alive frontend && serving http://localhost:5173/; then
    echo "[frontend] :5173 already served by a process devctl didn't start — leaving it alone"
  else
    # --strictPort: fail loudly instead of drifting to 5174 (backend CORS only
    # allows :5173 by default).
    start frontend frontend ./node_modules/.bin/vite --strictPort
    wait_http frontend http://localhost:5173/ 60
  fi

  echo "Stack up. Logs: .artifacts/logs/{backend,worker,frontend}.log"
}

cmd_down() {
  for name in $SERVICES; do
    if alive "$name"; then
      kill "$(cat "$RUNDIR/$name.pid")" 2>/dev/null || true
      echo "[$name] stopped"
    else
      echo "[$name] not running"
    fi
    rm -f "$RUNDIR/$name.pid"
  done
}

cmd_status() {
  for name in $SERVICES; do
    case $name in
    backend) url=http://localhost:8000/ ;;
    frontend) url=http://localhost:5173/ ;;
    *) url= ;;
    esac
    if alive "$name"; then
      state="running under devctl (pid $(cat "$RUNDIR/$name.pid"))"
    elif [ -n "$url" ] && serving "$url"; then
      state="serving, but NOT started by devctl (make dev / browser preview / terminal)"
    else
      state="stopped"
    fi
    if [ -n "$url" ]; then
      if serving "$url"; then
        state="$state — $url reachable"
      else
        state="$state — $url NOT reachable"
      fi
    fi
    echo "[$name] $state"
  done
}

cmd_logs() { # logs [name] [lines]
  name=${1:-}
  lines=${2:-40}
  if [ -n "$name" ]; then
    tail -n "$lines" "$LOGDIR/$name.log"
    return 0
  fi
  for n in $SERVICES; do
    [ -f "$LOGDIR/$n.log" ] || continue
    echo "===== $n (.artifacts/logs/$n.log) ====="
    tail -n "$lines" "$LOGDIR/$n.log"
    echo ""
  done
}

case ${1:-} in
up) cmd_up ;;
down) cmd_down ;;
status) cmd_status ;;
logs)
  shift
  cmd_logs "$@"
  ;;
*)
  echo "usage: $0 up|down|status|logs [name] [lines]" >&2
  exit 2
  ;;
esac
