# Claude Code skills

Skills that encode this stack's *process* conventions, tracked here rather than only in
`~/.claude/skills/` so they are versioned, reviewable, and available on a fresh machine.

| skill | what it does |
|---|---|
| `extract-to-foundation` | Walks an extraction through the blueprint §17 gates, and refuses to write a proposal before the two-call-site evidence exists. |

## Install

Claude Code discovers skills in `~/.claude/skills/` (every project) or a project's
`.claude/skills/` (that project only). These want to be available everywhere — an
extraction is proposed from whichever consumer repo you happen to be sitting in, not from
this one — so symlink them into the user-level directory:

```sh
ln -s "$PWD/skills/extract-to-foundation" ~/.claude/skills/extract-to-foundation
```

Symlink rather than copy, so editing the skill and committing it are the same act. A copy
drifts silently, and a drifted process skill is worse than none — it will confidently cite
gates that have since changed.

Verify it resolves:

```sh
head -3 ~/.claude/skills/extract-to-foundation/SKILL.md
```

Skills are picked up per session; start a new one after linking.
