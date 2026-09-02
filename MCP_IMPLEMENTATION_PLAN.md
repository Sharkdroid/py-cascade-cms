# cascade-cms-rest-mcp — Implementation Plan

**Status**: Ready for implementation
**Target repo**: `py-cascade-cms`
**Author**: Design locked via conversation with Keith (Claude, 2026-09-01)
**Implementer**: Claude Code

---

## 1. Purpose

A local, read-only, stdio-transport MCP server that wraps `cascade_cms` so an
AI agent (Claude Code, Claude Desktop, or any other MCP client) can inspect a
real Cascade CMS server — folder structure, asset schemas, structured data
fields, page configurations — to write more accurate scripts against the
library, without ever handling Cascade credentials directly.

**Primary consumers**: the `cascade-cms-script-writer` skill (better
grounding for generated scripts) and future maintainers of `py-cascade-cms`
who need to explore a live server's shape.

## 2. Non-Goals (v1)

- **No write operations.** No `create`, `edit`, `delete`, `publish`,
  `move`, `copy`, workflow transitions, or access-rights changes. Read-only,
  full stop.
- **No dataDefinition/format XML schema parsing.** `cascade_get_data_structure`
  and `cascade_get_page_config` report *instance-observed* groups/configs
  (from the one asset queried), not the full schema-valid set from the
  asset's bound definition/format asset. This is deferred — see §7.
- **No separate PyPI package.** This ships as an optional extra of the
  existing `cascade-cms-rest` distribution, not its own listing.
- **No hosted/remote transport.** stdio only. No SSE, no auth flows beyond
  the credentials the library already expects.

## 3. Distribution & Packaging

- Lives at `py-cascade-cms/mcp/` (sibling to `skill/`), **inside** the
  existing repo and existing `cascade-cms-rest` package — not a new PyPI
  project.
- Add an optional dependency group to the existing `pyproject.toml`:
  ```toml
  [project.optional-dependencies]
  mcp = ["mcp>=1.0.0"]  # confirm exact FastMCP/mcp SDK dependency at implementation time

  [project.scripts]
  cascade-cms-rest-mcp = "cascade_cms.mcp.server:main"
  ```
- Install path for users: `pip install cascade-cms-rest[mcp]`
- Client config invocation (document this exactly, it's a common copy-paste
  point of failure):
  ```json
  {
    "mcpServers": {
      "cascade-cms": {
        "command": "uvx",
        "args": ["--from", "cascade-cms-rest[mcp]", "cascade-cms-rest-mcp"],
        "env": {
          "CASCADE_API_URL": "...",
          "CASCADE_API_KEY": "..."
        }
      }
    }
  }
  ```
- **Note**: since `mcp/` sits under `src/cascade_cms/`, confirm at
  implementation time whether it should be `src/cascade_cms/mcp/` (importable
  as `cascade_cms.mcp`) — this is the assumed layout below. Adjust the
  entry-point path if a different location is chosen.

## 4. Framework & Credentials

- **Framework**: FastMCP (via the official `mcp` Python SDK).
- **Credentials**: read via `os.environ`, using **the same variable names
  `CascadeWrapperBase`'s `env_vars`/`config_vars` already expect** — no
  translation layer, no new naming scheme. The MCP server constructs a
  `CascadeWrapperBase` exactly as any other script would.
- Fail fast at server startup (not on first tool call) if required
  credentials are missing, with a message naming the exact expected env
  var(s).

## 5. Directory Layout

```
py-cascade-cms/
├── pyproject.toml                  # + [project.optional-dependencies].mcp, + console script
├── src/cascade_cms/
│   ├── ... (existing — untouched)
│   └── mcp/
│       ├── __init__.py
│       ├── server.py               # FastMCP instance, tool registration, main() entrypoint
│       ├── formatting.py           # concise/detailed collapse logic, name-from-path derivation
│       ├── errors.py               # ToolError helpers + self-contained group/config discovery
│       └── config.py               # env var loading, matches CascadeWrapperBase's expectations
├── skill/
└── tests/mcp/                      # new — smoke tests, run incrementally per phase
```

## 6. Tool Specification (locked)

All tools are read-only. All tool names are namespaced with a `cascade_`
prefix. All errors are returned as MCP tool errors (`isError: true` /
`ToolError`), never raised as bare exceptions — short, actionable, and
self-correcting where possible.

### 6.1 `cascade_search`

Wraps `Operations.search`. Cascade's raw `search` response (`ListElements`,
`elements: list[IdentifierType]`) has **no title/name field** — only `id`,
`asset_type`, and a `path` dict (`path` string + `siteName`). Derive a
pseudo-name from the last path segment; no extra `read()` calls needed.

- **Params**: `query: str` (→ `searchTerms`), `site: str` (→ `siteName`),
  `asset_types: list[str] | None = None` (→ `searchTypes`),
  `limit: int = 20`
- **Returns**:
  ```json
  {
    "results": [
      {"id": "<uuid>", "type": "<assetType>", "site": "<siteName>", "path": "<path>", "name": "<derived from last path segment>"}
    ],
    "total_count": 0,
    "has_more": false
  }
  ```
- **Errors**: Cascade `success: false` → `ToolError(message)`, passing
  Cascade's own message through (it's typically already actionable).

### 6.2 `cascade_read_asset`

Wraps `Operations.read`. The core entry point — agents should call this
before anything else.

- **Params**: `identifier` (id+type or path+site+type — mirror the
  library's existing `IdentifierType | Path` union), `format: Literal["concise", "detailed"] = "concise"`
- **Concise collapse rule** (general — applies uniformly, not just to known
  fields): for each top-level key in `Asset._data`,
  - if the value is a scalar (`str`, `int`, `float`, `bool`, `uuid`, `None`)
    → include as-is
  - if the value is a `dict` or `list` → collapse to:
    ```json
    {"_collapsed": true, "count": <int>, "expand_with": "<tool name>"}
    ```
    - `structuredData`-shaped fields → `expand_with: "cascade_get_data_structure"`
    - `pageConfigurations` → `expand_with: "cascade_get_page_config"`
    - anything else (e.g. `metadata`) → `expand_with: "cascade_read_asset(format=\"detailed\")"`
      since there's no dedicated expander tool for it
- **Detailed**: returns the raw `Asset._data` dict, unmodified.
- **Errors**: not found → `ToolError` suggesting the agent try
  `cascade_search` first.

### 6.3 `cascade_get_data_structure`

Wraps `Asset.get_data_structure(group, identifier)`.

- **Params**: `identifier`, `group: str`, `node_identifier: str`
- **Returns**: matched node(s), raw (these are targeted/small, no
  collapsing needed).
- **Errors**: not found → `ToolError` listing available group names on that
  asset. **This requires new logic, self-contained in the MCP server** (not
  added to the library) — a lightweight scan over `Asset._data` collecting
  `identifier` values wherever `type == "group"`, mirroring the shape of
  `Asset.get_data_structure`'s internal `find_group` walk but collecting
  rather than matching. Implement this as a private helper in
  `mcp/errors.py`, not as a change to `cmstypes.py`.
  - **Known limitation to document**: this reports only groups *present on
    this instance*, not the full schema-valid set from the asset's bound
    `dataDefinition`. See §7.

### 6.4 `cascade_get_page_config`

Wraps `Asset.get_page_configuration(configuration_name, page_region)`.

- **Params**: `identifier`, `configuration_name: str`, `page_region: str | None = None`
- **Returns**: the matched `PageConfiguration` or `PageRegion`, dumped to a
  plain dict (Pydantic `.model_dump()` or equivalent — these already have
  a defined shape, unlike the freeform structured-data nodes).
- **Errors**: not found → `ToolError` listing available configuration names.
  This one's cheaper than §6.3 — `Asset._page_configs` already holds the
  parsed list in memory (populated in `Asset.__init__`); just read `.name`
  off each entry.
  - **Same known limitation as §6.3** applies here too: instance-observed
    config names, not the full valid set from the asset's bound
    format/block/template asset. See §7.

### 6.5 `cascade_root_container_id`

Wraps `Asset.root_container_id(asset_type)`. Straightforward 1:1 mapping.

- **Params**: `site_identifier`, `asset_type: Literal["datadefinition", "sharedfield", "folder"]`
- **Returns**: `{"container_id": "<uuid>"}`
- **Errors**: unmapped `asset_type` → `ToolError` listing the supported
  types (mirrors `Asset._ROOT_CONTAINER_FIELDS` keys); not a site asset →
  `ToolError` saying so explicitly.

### 6.6 `cascade_list_sites`

Wraps `Operations.listSites`. Straightforward 1:1 mapping.

- **Params**: none
- **Returns**: `{"sites": [...], "total_count": <int>}`
- **Errors**: none expected beyond the general Cascade-failure case (same
  pattern as §6.1).

## 7. Known Limitations / Future Work

**Instance-observed vs. schema-authoritative field discovery.** Both
`cascade_get_data_structure` (§6.3) and `cascade_get_page_config` (§6.4)
currently report field/group/config names by sampling the *one asset
instance* the agent asked about — meaning an agent only learns about fields
that happen to be populated on that particular asset, not the full set of
fields the asset's type actually allows.

A likely stronger approach, deferred for a dedicated follow-up design
conversation: read the asset's **bound definition asset directly** —
- structured data groups/identifiers come from the asset's `dataDefinition`
  (XML schema — authoritative, shows every valid field regardless of
  whether any given instance populates it)
- page configuration names/regions come from the asset's bound
  format/block/template asset

Both of these definition/format assets are themselves ordinary Cascade
assets, reachable via the same `read()` operation already wrapped by
`cascade_read_asset` — so this likely does **not** require new library
capability, just MCP-layer logic to resolve which definition asset a given
asset is bound to and read that instead of (or in addition to) sampling the
instance. This needs its own dedicated conversation before implementation —
particularly around how to resolve the binding and how to parse/present the
XML schema shape without exploding token cost.

Document this limitation plainly in the MCP server's README/docs, with a
pointer to this section, so users aren't surprised by incomplete
group/config listings on sparsely-populated assets.

## 8. Phased Build Order

Structured so each phase is independently testable against a real dev
Cascade site before moving to the next — per Keith's preference to test
incrementally with Claude Code rather than review one large drop.

1. **Phase 1 — Read backbone**: `cascade_search` + `cascade_read_asset`
   (concise + detailed). This is the core loop; get it solid first.
2. **Phase 2 — Structured inspection**: `cascade_get_data_structure` +
   `cascade_get_page_config`, including their self-contained error/discovery
   paths (§6.3, §6.4).
3. **Phase 3 — Remaining reads**: `cascade_root_container_id` +
   `cascade_list_sites`.
4. **Phase 4 — Packaging**: `pyproject.toml` optional-dependency group,
   console script entry point, credential wiring per §4, README/client-config
   documentation per §3.

## 9. Acceptance Criteria

- [ ] All six tools implemented per §6, matching signatures and error
      behavior exactly.
- [ ] `cascade_read_asset` concise mode never returns an uncollapsed
      `dict`/`list` value.
- [ ] Every `ToolError` message is self-correcting (names what was tried,
      what's actually available, and/or which tool to call next) — never a
      bare "not found" or raw traceback.
- [ ] No tool in this server can perform a write operation, even indirectly.
- [ ] `pip install cascade-cms-rest[mcp]` installs everything needed; base
      `cascade-cms-rest` install remains unaffected (no new hard
      dependency).
- [ ] Credentials are read from the same env var names `CascadeWrapperBase`
      already expects — zero new credential-naming surface.
- [ ] README documents the known instance-observed-vs-schema limitation
      (§7) and the exact `uvx` client-config invocation (§3).
- [ ] Each phase in §8 has a corresponding smoke test under `tests/mcp/`
      that Keith can run against a real dev site.
