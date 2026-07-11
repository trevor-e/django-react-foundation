#!/usr/bin/env python3
"""Mark OpenSpec change tasks complete without ad-hoc inline scripting.

Flips `- [ ] <id> ` checkboxes to `- [x] <id> ` in a change's tasks.md (or back,
with --undo). Exists so agent sessions can update task state through an approved,
reviewable entry point instead of one-off python/sed commands that each require a
permission prompt.

Usage:
    scripts/claude/openspec-mark-tasks.py <change-name> <task-id> [<task-id> ...]
    scripts/claude/openspec-mark-tasks.py add-portfolio-tracking 1.1 1.2 2.3
    scripts/claude/openspec-mark-tasks.py <change-name> --undo 1.1
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("change", help="change name under openspec/changes/")
    parser.add_argument("task_ids", nargs="+", help="task ids, e.g. 1.1 2.3")
    parser.add_argument(
        "--undo", action="store_true", help="mark tasks incomplete instead"
    )
    args = parser.parse_args()

    tasks_file = REPO_ROOT / "openspec" / "changes" / args.change / "tasks.md"
    if not tasks_file.is_file():
        print(f"error: {tasks_file} not found", file=sys.stderr)
        return 1

    src, dst = ("[x]", "[ ]") if args.undo else ("[ ]", "[x]")
    text = tasks_file.read_text()
    missing: list[str] = []
    for task_id in args.task_ids:
        pending, done = f"- {src} {task_id} ", f"- {dst} {task_id} "
        if pending in text:
            text = text.replace(pending, done, 1)
            print(f"{task_id}: {'unmarked' if args.undo else 'marked done'}")
        elif done in text:
            print(f"{task_id}: already in target state")
        else:
            missing.append(task_id)

    tasks_file.write_text(text)
    if missing:
        print(f"error: task id(s) not found: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
