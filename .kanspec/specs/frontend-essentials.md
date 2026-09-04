---
feature: Runtime configuration, query keys, theme preferences, and brand-free auth UI
code: [src/apiConfig.ts, src/queryKeys.ts, src/theme.ts, src/authUi.tsx, src/index.ts]
---
# frontend-essentials

## Rules
- [frontend-essentials.config-inputs] API URL resolution receives build mode and candidate URLs as plain inputs, stays bundler-agnostic, and lets an explicit mode override the bundler's production flag. {pre-kanspec}
- [frontend-essentials.query-keys] Query keys are hierarchical and stable, exposing resource, list, and detail prefixes suitable for targeted TanStack Query invalidation. {pre-kanspec}
- [frontend-essentials.theme-storage] Theme preference is `system`, `light`, or `dark`; it lives in localStorage rather than cookies, and absent storage means `system`. {pre-kanspec}
- [frontend-essentials.theme-live] System preference follows OS changes live, while explicit light/dark preferences update the document root and synchronize across tabs. {pre-kanspec}
- [frontend-essentials.auth-ui] Auth UI is brand-free and opt-in through its own subpath so headless consumers do not resolve the optional Radix peer dependencies. {pre-kanspec}
- [frontend-essentials.root-export] The package root remains React-free; React-dependent SEO and auth UI are exposed only from opt-in subpaths. {pre-kanspec}
