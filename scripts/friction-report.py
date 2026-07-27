#!/usr/bin/env python3
"""Scan recent Claude Code transcripts for friction points.

Reads the JSONL session transcripts under ~/.claude/projects/<project-slug>/
and reports, per session and in aggregate:

  - tool errors (is_error tool_results), grouped by tool + error signature
  - permission denials / user rejections
  - user interruptions (esc during a turn)
  - commands that failed repeatedly with the same error (top offenders)

Usage:
  scripts/friction-report.py [--days N] [--project-dir PATH] [--top N] [-v]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

def default_project_dir(cwd: Path | None = None) -> Path:
    """Claude Code stores transcripts under a slug of the project's absolute path,
    with every non-alphanumeric character replaced by a dash. Deriving it from the
    working directory keeps this script project-agnostic; pass --project-dir to point
    it somewhere else."""
    root = Path.cwd() if cwd is None else cwd
    slug = re.sub(r"[^a-zA-Z0-9]", "-", str(root.resolve()))
    return Path.home() / ".claude" / "projects" / slug

DENIAL_MARKERS = (
    "The user doesn't want to proceed",
    "User rejected",
    "user doesn't want to take this action",
    "Permission to use tool",
)
INTERRUPT_MARKERS = (
    "[Request interrupted by user",
)
# Noise: errors that are expected/benign and not worth fixing.
IGNORE_ERROR_PATTERNS = (
    re.compile(r"^File does not exist"),  # normal exploration misses
    re.compile(r"has not been read yet"),
)


ERRORISH = re.compile(
    r"error|fail|exception|traceback|denied|not found|refused|timeout|cannot|unable",
    re.IGNORECASE,
)


def error_signature(text: str) -> str:
    """Normalize an error message so similar failures group together."""
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return "(empty)"
    first_line = lines[0]
    # Bash failures start with a bare "Exit code N" — pick the first line that
    # actually describes the error instead.
    if re.fullmatch(r"Exit code \d+", first_line.strip()):
        first_line = next(
            (ln for ln in lines[1:] if ERRORISH.search(ln)),
            lines[1] if len(lines) > 1 else first_line,
        )
    sig = re.sub(r"/[\w\-./]+", "<path>", first_line.strip())
    sig = re.sub(r"\d+", "<n>", sig)
    return sig[:160]


def content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def analyze_file(path: Path) -> dict:
    tool_names: dict[str, str] = {}  # tool_use_id -> tool name
    tool_inputs: dict[str, str] = {}  # tool_use_id -> short input repr
    errors: list[dict] = []
    denials: list[dict] = []
    interrupts = 0
    first_prompt = None

    with path.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content")

            if role == "user" and first_prompt is None and isinstance(content, str):
                if not rec.get("isSidechain"):
                    first_prompt = content.strip().replace("\n", " ")[:110]

            if isinstance(content, str) and role == "user":
                if any(m in content for m in INTERRUPT_MARKERS):
                    interrupts += 1
                continue
            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    tid = block.get("id", "")
                    tool_names[tid] = block.get("name", "?")
                    inp = block.get("input") or {}
                    short = (
                        inp.get("command")
                        or inp.get("file_path")
                        or inp.get("pattern")
                        or inp.get("url")
                        or ""
                    )
                    tool_inputs[tid] = str(short).replace("\n", " ")[:140]
                elif btype == "tool_result":
                    tid = block.get("tool_use_id", "")
                    text = content_text(block.get("content"))
                    if any(m in text for m in INTERRUPT_MARKERS):
                        interrupts += 1
                    if any(m in text for m in DENIAL_MARKERS):
                        denials.append(
                            {
                                "tool": tool_names.get(tid, "?"),
                                "input": tool_inputs.get(tid, ""),
                            }
                        )
                        continue
                    if block.get("is_error"):
                        if any(p.search(text.strip()) for p in IGNORE_ERROR_PATTERNS):
                            continue
                        errors.append(
                            {
                                "tool": tool_names.get(tid, "?"),
                                "input": tool_inputs.get(tid, ""),
                                "sig": error_signature(text),
                                "sidechain": bool(rec.get("isSidechain")),
                            }
                        )

    return {
        "file": path.name,
        "prompt": first_prompt or "(no top-level user prompt)",
        "errors": errors,
        "denials": denials,
        "interrupts": interrupts,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=3, help="look back N days (default 3)")
    ap.add_argument("--project-dir", type=Path, default=None)
    ap.add_argument("--top", type=int, default=15, help="top N error signatures")
    ap.add_argument("-v", "--verbose", action="store_true", help="per-session detail")
    args = ap.parse_args()
    project_dir = args.project_dir or default_project_dir()

    if not project_dir.is_dir():
        print(f"No transcript directory at {project_dir} — pass --project-dir.")
        return 1

    cutoff = time.time() - args.days * 86400
    files = sorted(
        (p for p in project_dir.glob("*.jsonl") if p.stat().st_mtime >= cutoff),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        print(f"No transcripts in the last {args.days:g} days under {project_dir}")
        return 1

    sessions = [analyze_file(p) for p in files]

    sig_counter: Counter[tuple[str, str]] = Counter()
    sig_examples: dict[tuple[str, str], str] = {}
    denial_counter: Counter[str] = Counter()
    denial_examples: defaultdict[str, list[str]] = defaultdict(list)
    total_errors = total_denials = total_interrupts = 0

    for s in sessions:
        total_errors += len(s["errors"])
        total_denials += len(s["denials"])
        total_interrupts += s["interrupts"]
        for e in s["errors"]:
            key = (e["tool"], e["sig"])
            sig_counter[key] += 1
            sig_examples.setdefault(key, e["input"])
        for d in s["denials"]:
            denial_counter[d["tool"]] += 1
            if len(denial_examples[d["tool"]]) < 3:
                denial_examples[d["tool"]].append(d["input"])

    print(
        f"Scanned {len(sessions)} sessions from the last {args.days:g} days: "
        f"{total_errors} tool errors, {total_denials} permission denials, "
        f"{total_interrupts} user interruptions\n"
    )

    if sig_counter:
        print(f"Top {args.top} error signatures (tool | count | signature):")
        for (tool, sig), n in sig_counter.most_common(args.top):
            print(f"  {n:3d}x  {tool:14s} {sig}")
            ex = sig_examples[(tool, sig)]
            if ex:
                print(f"        e.g. {ex}")
        print()

    if denial_counter:
        print("Permission denials by tool:")
        for tool, n in denial_counter.most_common():
            print(f"  {n:3d}x  {tool}")
            for ex in denial_examples[tool]:
                print(f"        e.g. {ex}")
        print()

    if args.verbose:
        print("Per-session detail:")
        for s in sessions:
            flags = []
            if s["errors"]:
                flags.append(f"{len(s['errors'])} errors")
            if s["denials"]:
                flags.append(f"{len(s['denials'])} denials")
            if s["interrupts"]:
                flags.append(f"{s['interrupts']} interrupts")
            if not flags:
                continue
            print(f"  {s['file']}  [{', '.join(flags)}]")
            print(f"    prompt: {s['prompt']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
