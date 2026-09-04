---
feature: Authenticated MCP tools and OAuth connectivity for AI clients
code: [python/src/drf_foundation/mcp/**, python/tests/test_mcp_*.py]
---
# mcp-server

## Rules
- [mcp-server.transport] MCP is served as tools-only streamable HTTP JSON-RPC with bounded request bodies and protocol-shaped errors, including HTTP failures. {pre-kanspec}
- [mcp-server.credentials] API keys and OAuth access tokens share a hashed-at-rest credential base; raw secrets are shown only when minted and revoked credentials cannot authenticate. {pre-kanspec}
- [mcp-server.tenant-source] Tenant or resource identity comes from the authenticated credential, never from an MCP path or a tool argument, and lookups remain scoped so foreign identifiers appear not found. {pre-kanspec}
- [mcp-server.write-scope] Tool handlers call shared service functions, declare whether they write, and write tools are refused unless the authenticated context has write authority. {pre-kanspec}
- [mcp-server.oauth] OAuth enforces PKCE, exact redirect matching, single-use authorization codes, replacement of prior grants, and HTTPS issuers in production. {pre-kanspec}
- [mcp-server.client-identity] Client ID Metadata Documents are fetched through the guarded no-redirect public-network seam and revalidated; dynamic registration remains the compatibility fallback. {pre-kanspec}
- [mcp-server.rate-signals] When key throttling is enabled, every response exposes limit, remaining, and reset headers; 429 responses also include `Retry-After`. {pre-kanspec}
