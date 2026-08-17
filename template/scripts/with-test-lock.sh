#!/bin/sh
# Serialize backend test runs across checkouts/worktrees.
#
# They all share one Dockerized test Postgres (fixed container name + fixed port), so
# concurrent runs clobber each other's test database — and `make test-backend`'s
# automatic teardown kills the container out from under the other run. That failure is
# confusing precisely when you're most likely to hit it: two agents, or an agent and a
# human, testing the same repo at once.
#
# Blocks until the lock frees, then runs the given command; the lock releases when the
# command exits. Override the lock file with TEST_LOCK_FILE if a machine runs several
# unrelated projects that should not contend with each other.
#
# (python locking rather than flock(1) because macOS doesn't ship flock; msvcrt
# branch because Windows doesn't ship fcntl. python3 fallback: Windows installs
# often expose only `python`.)
PY=$(command -v python3 || command -v python)
exec "$PY" - "$@" <<'EOF'
import os
import subprocess
import sys
import time

default_dir = os.environ.get("TEMP", "/tmp") if os.name == "nt" else "/tmp"
lock_path = os.environ.get(
    "TEST_LOCK_FILE", os.path.join(default_dir, "__PROJECT__-backend-test.lock")
)
f = open(lock_path, "w")

if os.name == "nt":
    import msvcrt

    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print(
            "[test-lock] another backend test run holds the test DB — waiting for it to finish...",
            flush=True,
        )
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(1)
else:
    import fcntl

    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(
            "[test-lock] another backend test run holds the test DB — waiting for it to finish...",
            flush=True,
        )
        fcntl.flock(f, fcntl.LOCK_EX)

sys.exit(subprocess.call(sys.argv[1:]))
EOF
