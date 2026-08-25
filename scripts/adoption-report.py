#!/usr/bin/env python3
"""Report how far each consumer has drifted from this repo.

Answers the two questions that went unasked long enough to cost real time:

  1. **Is anyone behind?** adulting sat on py-v0.19.0 while py-v0.19.2 carried a fix for
     a 500 on the password-reset path. Nothing said so.
  2. **Is anyone running a local copy of a shared module?** `crons` and `ops_status` were
     extracted *from* adulting and then duplicated there for a month, because "both
     consumers adopt before the next extraction" (blueprint §17 Gate 3) was a habit rather
     than a check.

Also flags single-consumer modules, which are shared only in the sense that they live in
the shared repo — their interface has never been stressed by a second caller.

Usage:
  scripts/adoption-report.py                  # default consumers
  scripts/adoption-report.py --consumer ~/dev/foo --consumer ~/dev/bar
  scripts/adoption-report.py --exit-code      # non-zero if anything is behind (for CI)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

FOUNDATION = Path(__file__).resolve().parent.parent
DEFAULT_CONSUMERS = [Path.home() / "dev" / "adulting", Path.home() / "dev" / "pystonks"]

PY_PIN = re.compile(r'rev\s*=\s*"(py-v[0-9][^"]*)"')
JS_PIN = re.compile(r'"react-vite-foundation"\s*:\s*"[^"#]*#(js-v[0-9][^"]*)"')


def git(*args: str, cwd: Path = FOUNDATION) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    ).stdout.strip()


def version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", tag))


def latest_tag(prefix: str) -> str | None:
    tags = [t for t in git("tag", "--list", f"{prefix}*").splitlines() if t.strip()]
    return max(tags, key=version_key) if tags else None


def find_pin(consumer: Path, pattern: re.Pattern[str], *relatives: str) -> tuple[str, str] | None:
    for rel in relatives:
        path = consumer / rel
        if not path.exists():
            continue
        match = pattern.search(path.read_text())
        if match:
            return match.group(1), rel
    return None


def releases_between(old: str, new: str, prefix: str) -> list[str]:
    """Tags strictly after `old` up to and including `new`."""
    tags = [t for t in git("tag", "--list", f"{prefix}*").splitlines() if t.strip()]
    lo, hi = version_key(old), version_key(new)
    return sorted((t for t in tags if lo < version_key(t) <= hi), key=version_key)


#: Modules a consumer never names in an import, so grep cannot see the usage.
#: `pytest_plugin` is a pytest11 entry point — its fixtures are injected by name.
INVISIBLE_TO_GREP = {
    "pytest_plugin": "pytest11 entry point; fixtures are injected, never imported",
}


def imported_modules(consumer: Path) -> set[str]:
    """Which `drf_foundation.<module>` names a consumer's *source* refers to.

    Restricted to Python files on purpose. Counting every tracked file reports a module
    as adopted because an archived OpenSpec proposal or an architecture doc mentions it
    by name, which is how this script would have told you adulting used `event_log` and
    `celery_health` when it imports neither.
    """
    out = subprocess.run(
        ["git", "grep", "-ho", r"drf_foundation[.][a-z_]\+", "--", "*.py"],
        cwd=consumer, capture_output=True, text=True, check=False,
    ).stdout
    return {line.split(".", 1)[1] for line in out.splitlines() if "." in line}


def package_modules() -> set[str]:
    """Importable modules only — a data directory like `templates/` is not one."""
    src = FOUNDATION / "python" / "src" / "drf_foundation"
    names = {p.stem for p in src.glob("*.py") if p.stem != "__init__"}
    names |= {
        p.name for p in src.iterdir()
        if p.is_dir() and not p.name.startswith("__") and (p / "__init__.py").exists()
    }
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--consumer", action="append", type=Path, default=None)
    parser.add_argument("--exit-code", action="store_true",
                        help="exit non-zero if any consumer is behind")
    args = parser.parse_args()
    consumers = [p.expanduser() for p in (args.consumer or DEFAULT_CONSUMERS) if p.expanduser().exists()]

    behind = False
    print("=" * 72)
    print("RELEASES")
    print("=" * 72)
    for prefix, pattern, relatives in (
        ("py-v", PY_PIN, ("backend/pyproject.toml", "pyproject.toml")),
        ("js-v", JS_PIN, ("frontend/package.json", "package.json")),
    ):
        latest = latest_tag(prefix)
        if latest is None:
            continue
        unreleased = git("log", "--oneline", f"{latest}..HEAD").splitlines()
        note = f"  ({len(unreleased)} unreleased commit(s) on main)" if unreleased else ""
        print(f"\n{prefix}* latest: {latest}{note}")
        for consumer in consumers:
            found = find_pin(consumer, pattern, *relatives)
            if found is None:
                print(f"  {consumer.name:<12} — no pin found")
                continue
            pin, where = found
            if pin == latest:
                print(f"  {consumer.name:<12} {pin}  up to date")
                continue
            behind = True
            missed = releases_between(pin, latest, prefix)
            print(f"  {consumer.name:<12} {pin}  BEHIND by {len(missed)}: {', '.join(missed)}")
            for tag in missed:
                for line in git("log", "--oneline", f"{tag}^..{tag}").splitlines()[:1]:
                    print(f"      {tag}: {line}")
            print(f"      (pinned in {where})")

    print()
    print("=" * 72)
    print("MODULE ADOPTION  — a shared module only one consumer imports")
    print("=" * 72)
    usage = {c.name: imported_modules(c) for c in consumers}
    names = sorted(package_modules())
    single, unused = [], []
    for name in names:
        users = [c for c, mods in usage.items() if name in mods]
        if not users:
            unused.append(name)
        elif len(users) < len(consumers):
            single.append((name, users))

    if single:
        print("\nSingle-consumer (interface never stressed by a second caller;")
        print("check whether the other project is running a local copy — §17 Gate 3):")
        for name, users in single:
            missing = [c for c in usage if c not in users]
            print(f"  {name:<20} used by {', '.join(users)}  |  not by {', '.join(missing)}")
    unused = [n for n in unused if n not in INVISIBLE_TO_GREP]
    if unused:
        print("\nNo importers at all (built ahead of demand, or dead):")
        for name in unused:
            print(f"  {name}")
    if INVISIBLE_TO_GREP:
        print("\nNot checkable by grep, verify by hand:")
        for name, why in sorted(INVISIBLE_TO_GREP.items()):
            print(f"  {name:<20} {why}")
    if not single and not unused:
        print("\nEvery module is imported by every consumer.")

    print()
    if behind and args.exit_code:
        print("FAIL: at least one consumer is behind the latest release.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
