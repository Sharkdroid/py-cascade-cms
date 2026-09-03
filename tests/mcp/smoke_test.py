import os
import re

"""
Title: Manual Human test - MCP Phase 1 + Phase 2 + Phase 3 smoke test
Date:
Description: Run directly (`python tests/mcp/smoke_test.py`), not via pytest,
against a real dev Cascade site, to sanity-check all six tools end-to-end
before wiring the server into an actual MCP client. Fill in SITE/QUERY and the
env vars below with real dev-site values first (or export
CASCADE_API_KEY/CASCADE_URL/SERVER before running). For the Phase 2 portion,
QUERY should turn up at least one data-bound page asset (one that uses a
Cascade Content Type), so pick a site/term accordingly.

This is also the moment to confirm several things no fixture in this repo can:
- The "structuredData" key-name assumption in mcp/formatting.py: does a real
  page's concise cascade_read_asset() output route a structured-data field to
  expand_with: "cascade_get_data_structure"? If the raw detailed payload uses
  a different key name, formatting.py's _EXPAND_HINT_BY_KEY needs updating.
- Whether cascade_read_asset's concise output on a page carries a `_references`
  entry for `contentType` (confirms references.py fires on real data, not just
  the fixture payloads).
- Whether any real asset ever carries a *direct* `dataDefinitionId` field
  (resolution.py's short-circuit path - only a fixture-free guess so far).
- Whether cascade_get_data_structure/cascade_get_page_config's group/field/
  configuration listings look sane against real authored content.

Expected Output:
- cascade_search returns at least one result for the given query/site.
- cascade_read_asset(format="concise") never contains a raw (uncollapsed)
  dict/list value; carries a `_references` entry if the asset has any
  <field>Id/<field>Path pairs.
- cascade_read_asset(format="detailed") returns the full raw payload.
- cascade_get_data_structure returns a group's field listing, then one field's
  full definition.
- cascade_get_page_config returns the content type's configuration names.
- cascade_list_sites returns at least SITE among the real sites on this server.
- cascade_root_container_id returns a real container id for SITE's root folder.

Actual Output:


"""

os.environ.setdefault("CASCADE_API_KEY", "...")
os.environ.setdefault("CASCADE_URL", "https://cascadeapptest.csi.edu:8443")
os.environ.setdefault("SERVER", "APPTEST")

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from cascade_cms.cmstypes import IdentifierType  # noqa: E402
from cascade_cms.mcp.server import (  # noqa: E402
    cascade_get_data_structure,
    cascade_get_page_config,
    cascade_list_sites,
    cascade_read_asset,
    cascade_root_container_id,
    cascade_search,
)

SITE = "www.csi.edu"
QUERY = "apply/default"

sites = cascade_list_sites()
print(sites)

# A site's own `path` (per cascade_list_sites above) IS its name - not a
# path *within* itself - so addressing it by Path(path="", siteName=SITE)
# is wrong (Cascade 404s: "The requested asset does not exist"). Simplest
# correct approach: look the site up by id from the listing already in
# hand, same as any other asset.
site_match = next((s for s in sites["sites"] if s["name"] == SITE), None)
if site_match is None:
    print(f"Site {SITE!r} not found in cascade_list_sites output - adjust SITE above.")
else:
    site_container = cascade_root_container_id(
        site_identifier=IdentifierType(identifier=site_match["id"], asset_type="site"),
        asset_type="folder",
    )
    print(site_container)

results = cascade_search(query=QUERY, site=SITE)
print(results)

if results["results"]:
    first = results["results"][0]
    # Constructed directly as an IdentifierType, not a plain dict: calling
    # these tool functions straight from Python (as this script does)
    # bypasses the MCP protocol layer's dict-to-Pydantic coercion, which
    # only happens when a real MCP client invokes the tool over the wire.
    identifier = IdentifierType(identifier=first["id"], asset_type=first["type"])

    concise = cascade_read_asset(identifier=identifier, format="concise")
    print(concise)

    detailed = cascade_read_asset(identifier=identifier, format="detailed")
    print(detailed)

    references = concise.get("_references", {})
    content_type_ref = references.get("contentType")
    if content_type_ref is None:
        print(
            "No contentType reference on this asset - pick a data-bound page "
            "asset (SITE/QUERY above) to exercise cascade_get_data_structure/"
            "cascade_get_page_config."
        )
    else:
        configs = cascade_get_page_config(identifier=identifier)
        print(configs)

        if configs["configurations"]:
            config_name = configs["configurations"][0]["name"]
            config_detail = cascade_get_page_config(
                identifier=identifier, configuration_name=config_name
            )
            print(config_detail)

        # The real group identifiers live inside this content type's data
        # definition XML - not something this script can guess ahead of
        # time, and not something you look up by searching the CMS (the
        # tool resolves contentTypeId -> contenttype -> dataDefinitionId
        # itself from `identifier`, same as cascade_get_page_config above).
        # So: probe with a deliberately-wrong group name first. The
        # self-correcting ToolError lists every real group identifier -
        # that's the discovery mechanism, not cascade_search. Parsed here
        # against errors.group_not_found_error's exact wording (errors.py) -
        # re-check that format if this regex ever stops matching.
        try:
            cascade_get_data_structure(identifier=identifier, group="__probe__")
            group_name = None
        except ToolError as exc:
            print(f"[probe] {exc}")
            match = re.search(r"Available groups: (.+)\.$", str(exc))
            names = re.sub(r", and \d+ more$", "", match.group(1)) if match else ""
            group_name = names.split(", ")[0] if names else None

        if group_name:
            groups = cascade_get_data_structure(identifier=identifier, group=group_name)
            print(groups)
            # list_children mixes group and leaf entries in one flat listing;
            # find_node only ever resolves a leaf (tag != "group") - a group
            # entry needs its own top-level group=<identifier> call instead
            # (find_group searches the whole tree, not just this group's
            # children, so no path is needed).
            leaf = next(
                (c for c in groups["children"] if c["tag"] != "group"), None
            )
            if leaf:
                node = cascade_get_data_structure(
                    identifier=identifier, group=group_name, node_identifier=leaf["identifier"]
                )
                print(node)
else:
    print("No search results - adjust SITE/QUERY above and re-run.")
