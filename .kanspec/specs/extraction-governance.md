---
feature: Evidence-based extraction and adoption gates for shared foundation changes
code: [docs/blueprint.md, scripts/adoption-report.py, scripts/friction-report.py]
---
# extraction-governance

## Rules
- [extraction-governance.two-consumers] Shared behavior is extracted only after two real consumer projects independently demonstrate the same need. {pre-kanspec}
- [extraction-governance.behavior-first] Extraction preserves observable behavior before improving the abstraction; wire payloads and serialized representations remain compatible through the move. {pre-kanspec}
- [extraction-governance.no-product-policy] The foundation owns reusable mechanism, while branding, tenancy choices, concrete models/migrations, and product-specific flow decisions remain in consumers. {pre-kanspec}
- [extraction-governance.choke-point] External SDKs and delivery providers sit behind one seam that can be tested and mechanically enforced; consumers do not scatter direct imports. {pre-kanspec}
- [extraction-governance.reversible] An extraction remains independently reversible and does not force coupled data migrations or deploy ordering across consumers. {pre-kanspec}
- [extraction-governance.adoption-proof] The adoption report records consumer pins, duplicate local implementations, and required configuration so claimed adoption is evidence rather than intent. {pre-kanspec}
- [extraction-governance.exclusions] When a candidate fails a gate, document the exclusion as a failed gate and keep it project-local rather than weakening the gate to admit it. {pre-kanspec}
