---
feature: Kanspec-backed proposals, tickets, living specs, and standing rules for changes to the shared stack
code: [docs/blueprint.md, CLAUDE.md, AGENTS.md, scripts/new-project.sh, template/**]
---
# change-management

## Rules
- [change-management.nontrivial] Non-trivial changes use a Kanspec proposal before implementation; trivial or mechanical work may begin as a direct ticket. {pre-kanspec}
- [change-management.review] A proposal is reviewed and approved before its tickets are implemented, and unresolved review comments block approval. {pre-kanspec}
- [change-management.claim] Every implementation ticket is claimed with `kanspec start` before coding, shipped with its recorded PR, and closed only after Kanspec derives the merge from git. {pre-kanspec}
- [change-management.specs] Living specs change on the implementation branch when behavior changes; proposal close is not a deferred spec-sync step. {pre-kanspec}
- [change-management.standing] Only living specs, accepted decisions, and active quirks are standing guidance; closed proposal prose binds nothing. {pre-kanspec}
- [change-management.bootstrap] New projects initialize Kanspec and install both Claude and Codex context during bootstrap. {pre-kanspec}
