import { defineRailway, github, postgres, preserve, project, redis, service } from "railway/iac";

// Railway Infrastructure as Code (blueprint §11). This file is NOT read at
// deploy time — it mirrors platform state, and edits reach Railway via
// `railway config plan` / `railway config apply` from a linked checkout
// (`npm install` in .railway/ first; see .railway/README.md). Omit means
// delete: every service and variable this project owns stays declared here;
// secrets stay out of source as preserve() ("keep the value already set in
// Railway").
export default defineRailway(() => {
  // Set to the real GitHub repo so every push to main auto-deploys.
  // rootDirectory makes dockerfilePath resolve (and `railway up`, which always
  // archives from the repo root, still work as a fallback).
  const repo = github("OWNER/__PROJECT__", { rootDirectory: "backend" });

  const Postgres = postgres("Postgres");
  const Redis = redis("Redis");

  // One image, three PROCESS roles (§11, backend/entrypoint.sh). watchPatterns
  // keep frontend-only commits from redeploying the backend services.
  const backend = service("backend", {
    source: repo,
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile", watchPatterns: ["backend/**"] },
    // Web service only (§11b) — worker/beat expose no port, so a healthcheck
    // on them fails their deploys.
    healthcheck: "/api/health",
    // Migrations run here, before new containers take traffic. Kept behind the
    // PROCESS=web shell gate so a copy-paste onto worker/beat can't race
    // migrate against one Postgres ("relation already exists" fails the
    // losers' deploys).
    deploy: { preDeployCommand: ["sh -c 'if [ \"$PROCESS\" = web ]; then python manage.py migrate --noinput; fi'"], restartPolicyMaxRetries: 3 },
    // domains: ["api.example.com"],
    env: {
      PROCESS: "web",
      DATABASE_URL: Postgres.env.DATABASE_URL,
      REDIS_URL: Redis.env.REDIS_URL,
      DEBUG: "false",
      // Set the values on Railway (dashboard or `railway variables`);
      // preserve() keeps them there without writing them into source.
      // SECRET_KEY is a real secret; the rest are per-deploy config you may
      // inline as literals here instead.
      SECRET_KEY: preserve(),
      ALLOWED_HOSTS: preserve(),
      CORS_ALLOWED_ORIGINS: preserve(),
      FRONTEND_BASE_URL: preserve(),
    },
  });
  const worker = service("worker", {
    source: repo,
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile", watchPatterns: ["backend/**"] },
    deploy: { restartPolicyMaxRetries: 3 },
    env: {
      PROCESS: "worker",
      DATABASE_URL: Postgres.env.DATABASE_URL,
      REDIS_URL: Redis.env.REDIS_URL,
      // Reference the web service so signing keys and mode stay in sync.
      SECRET_KEY: backend.env.SECRET_KEY,
      DEBUG: backend.env.DEBUG,
    },
  });
  const beat = service("beat", {
    source: repo,
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile", watchPatterns: ["backend/**"] },
    deploy: { restartPolicyMaxRetries: 3 },
    env: {
      PROCESS: "beat",
      DATABASE_URL: Postgres.env.DATABASE_URL,
      REDIS_URL: Redis.env.REDIS_URL,
      SECRET_KEY: backend.env.SECRET_KEY,
      DEBUG: backend.env.DEBUG,
    },
  });

  return project("__PROJECT__", {
    resources: [backend, worker, beat, Postgres, Redis],
  });
});
