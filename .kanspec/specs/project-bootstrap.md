---
feature: Reproducible full-stack project scaffolding, CI, local services, and deployment wiring
code: [scripts/new-project.sh, template/**]
---
# project-bootstrap

## Rules
- [project-bootstrap.complete] One bootstrap command renders a runnable Django/DRF ASGI backend and React/Vite frontend with tests, CI, local service controls, and deployment configuration. {pre-kanspec}
- [project-bootstrap.clean-target] Bootstrap refuses a non-empty destination and fails when required tools are missing rather than merging a partial template into an existing project. {pre-kanspec}
- [project-bootstrap.identity] Project-name placeholders, package metadata, origins, and generated secrets are replaced for the new project; template placeholder text must not remain. {pre-kanspec}
- [project-bootstrap.git] The generated project is initialized on `main` with an initial commit before Kanspec initializes against that branch. {pre-kanspec}
- [project-bootstrap.kanspec] Bootstrap requires Kanspec, initializes its store, and installs both Claude and Codex agent context into the generated repository. {pre-kanspec}
- [project-bootstrap.same-server] Development and production use the same ASGI application/server shape; environment switches change services and configuration, not the application protocol. {pre-kanspec}
- [project-bootstrap.ci] Generated CI installs locked dependencies and gates formatting, types, tests, wire-schema drift, and email-preview drift; Kanspec doctor remains a local gate until the binary has a pinned install source. {pre-kanspec}
