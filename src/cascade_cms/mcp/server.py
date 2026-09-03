"""MCPServer instance and tool registration for cascade-cms-rest-mcp.

Phase 1: cascade_search, cascade_read_asset. Phase 2: cascade_get_data_structure,
cascade_get_page_config. Read-only, full stop - no tool in this module (or any
future one) may wrap a write operation.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from cascade_cms.cmstypes import (
    Asset,
    AssetTypes,
    CascadeError,
    IdentifierType,
    ListElements,
    SearchInformation,
)
from cascade_cms.cmstypes import Path as CascadePath
from cascade_cms.wrapper import CascadeWrapperBase

from . import data_structure, errors, formatting, resolution
from .config import cache_configuration, load_environment_variables

mcp = MCPServer(
    name="cascade-cms",
    instructions=(
        "Read-only inspection of a Hannon Hill Cascade CMS server. Call "
        "cascade_search first if you don't already know an asset's id/type/path; "
        'then cascade_read_asset to read it (format="concise" by default). For a '
        "data-bound asset's field schema or page configuration names, use "
        "cascade_get_data_structure / cascade_get_page_config - these resolve the "
        "asset's bound content type/data definition rather than sampling one "
        "instance, so they report the full schema-valid set of fields/configs."
    ),
)


def _wrapper() -> CascadeWrapperBase:
    """A fresh CascadeWrapperBase per tool call.

    CascadeCMSRestDriver owns a private, non-reentrant asyncio event loop per
    instance, and MCPServer can dispatch concurrent sync tool calls onto
    different worker threads - sharing one wrapper/driver across those would
    race. This also matches every existing precedent in the repo (README,
    skill templates, tests/edit_test.py): a short-lived `with
    CascadeWrapperBase(...) as cascade:` block per unit of work. The
    file-backed SQLite cache still persists hits across calls even though the
    connection object doesn't.
    """
    return CascadeWrapperBase(load_environment_variables(), cache_configuration())


@mcp.tool()
def cascade_search(
    query: str,
    site: str,
    asset_types: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Search a Cascade site for assets by text; returns id/type/path/name per match.

    total_count/has_more describe what Cascade returned in THIS response, not
    a true site-wide match count - Cascade's search API exposes no such count.
    """
    try:
        # asset_types is deliberately typed list[str] (not list[AssetTypes]) so
        # a caller isn't required to already know Cascade's full type-literal
        # set; an invalid value fails SearchInformation's own validation below
        # and is caught by the except Exception clause, surfacing as a
        # ToolError rather than silently doing the wrong thing.
        payload = SearchInformation(
            siteName=site,
            searchTerms=query,
            searchTypes=cast(
                "list[AssetTypes] | list[Literal['']]", asset_types or [""]
            ),
        )
        with _wrapper() as cascade:
            cascade.operations.search(payload)
            result = errors.single_result(
                cascade.submit_requests(), context="cascade_search"
            )

        if isinstance(result, CascadeError | Exception):
            raise errors.search_failure_error(result)

        assert isinstance(result, ListElements)
        return formatting.format_search_results(result, limit=limit)
    except ToolError:
        raise
    except Exception as exc:
        raise errors.unexpected_failure_error("cascade_search", exc) from exc


@mcp.tool()
def cascade_read_asset(
    identifier: IdentifierType | CascadePath,
    format: Literal["concise", "detailed"] = "concise",
) -> dict[str, Any]:
    """Read a single Cascade asset by id+type, or by site+path+type.

    format="concise" (default): large/nested fields come back collapsed with
    an expand_with hint. format="detailed": the raw asset payload, unmodified.
    """
    try:
        with _wrapper() as cascade:
            cascade.operations.read(identifier)
            result = errors.single_result(
                cascade.submit_requests(), context="cascade_read_asset"
            )

        if isinstance(result, CascadeError | Exception):
            raise errors.read_asset_error(identifier, result)

        assert isinstance(result, Asset)
        return formatting.format_asset(result, format=format)
    except ToolError:
        raise
    except Exception as exc:
        raise errors.unexpected_failure_error("cascade_read_asset", exc) from exc


@mcp.tool()
def cascade_get_data_structure(
    identifier: IdentifierType | CascadePath,
    group: str,
    node_identifier: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Get a data-bound asset's field schema for one group, resolved from its
    bound content type/data definition (schema-authoritative, not sampled from
    this one asset instance).

    node_identifier omitted: lists the group's immediate fields/subgroups.
    node_identifier given: returns that one field's full definition.
    """
    try:
        with _wrapper() as cascade:
            cascade.operations.read(identifier)
            result = errors.single_result(
                cascade.submit_requests(), context="cascade_get_data_structure"
            )
            if isinstance(result, CascadeError | Exception):
                raise errors.read_asset_error(
                    identifier, result, context="cascade_get_data_structure"
                )
            assert isinstance(result, Asset)
            data_definition = resolution.resolve_data_definition(
                result, cascade, context="cascade_get_data_structure"
            )

        xml_text = data_definition._data.get("xml")
        if not xml_text:
            raise errors.no_xml_field_error(
                data_definition, context="cascade_get_data_structure"
            )
        tree = data_structure.build_tree(xml_text)

        group_node = data_structure.find_group(tree, group)
        if group_node is None:
            raise errors.group_not_found_error(
                data_definition,
                data_structure.collect_group_identifiers(tree),
                group,
                context="cascade_get_data_structure",
            )

        meta = {"id": data_definition.get("id"), "path": data_definition.get("path")}
        expand_with = (
            'cascade_read_asset(identifier={"id": "'
            f'{data_definition.get("id")}", "type": "datadefinition"}}, format="detailed")'
        )

        if node_identifier is None:
            listing = formatting.truncate_list(
                data_structure.list_children(group_node),
                key="children",
                limit=limit or formatting.DEFAULT_LIST_LIMIT,
                expand_with=expand_with,
            )
            return {"group": group, "data_definition": meta, **listing}

        node = data_structure.find_node(group_node, node_identifier)
        if node is None:
            available = [
                c["identifier"]
                for c in data_structure.list_children(group_node)
                if c["identifier"]
            ]
            raise errors.node_not_found_error(
                data_definition,
                group,
                available,
                node_identifier,
                context="cascade_get_data_structure",
            )
        return {"group": group, "data_definition": meta, "node": node}
    except ToolError:
        raise
    except Exception as exc:
        raise errors.unexpected_failure_error(
            "cascade_get_data_structure", exc
        ) from exc


@mcp.tool()
def cascade_get_page_config(
    identifier: IdentifierType | CascadePath,
    configuration_name: str | None = None,
    page_region: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Get a data-bound asset's page configuration names, resolved from its
    bound content type (schema-authoritative), plus region content for a
    specific configuration on this asset instance (genuinely per-instance data,
    not schema).

    configuration_name omitted: lists available configuration names.
    configuration_name given, page_region omitted: names the regions actually
    authored on this instance for that configuration.
    Both given: returns that region's content.
    """
    try:
        if page_region is not None and configuration_name is None:
            raise errors.page_region_requires_configuration_name_error(
                context="cascade_get_page_config"
            )

        with _wrapper() as cascade:
            cascade.operations.read(identifier)
            result = errors.single_result(
                cascade.submit_requests(), context="cascade_get_page_config"
            )
            if isinstance(result, CascadeError | Exception):
                raise errors.read_asset_error(
                    identifier, result, context="cascade_get_page_config"
                )
            assert isinstance(result, Asset)
            asset = result
            content_type = resolution.resolve_content_type(
                asset, cascade, context="cascade_get_page_config"
            )

        configs = content_type._data.get("contentTypePageConfigurations") or []
        available = sorted(
            (
                {
                    "name": c.get("pageConfigurationName"),
                    "id": c.get("pageConfigurationId"),
                    "publish_mode": c.get("publishMode"),
                }
                for c in configs
            ),
            key=lambda c: c["name"] or "",
        )

        if configuration_name is None:
            listing = formatting.truncate_list(
                available,
                key="configurations",
                limit=limit or formatting.DEFAULT_LIST_LIMIT,
                expand_with=(
                    'cascade_read_asset(identifier={"id": "'
                    f'{content_type.get("id")}", "type": "contenttype"}}, format="detailed")'
                ),
            )
            return {
                "content_type": {
                    "name": content_type.get("name"),
                    "id": content_type.get("id"),
                },
                **listing,
            }

        match = next((c for c in available if c["name"] == configuration_name), None)
        if match is None:
            names = [c["name"] for c in available if c["name"]]
            raise errors.config_name_not_found_error(
                content_type,
                names,
                configuration_name,
                context="cascade_get_page_config",
            )

        instance_config = next(
            (c for c in asset._page_configs if c.name == configuration_name), None
        )
        region_names = (
            [r.name for r in instance_config.pageRegions] if instance_config else []
        )

        if page_region is None:
            return {
                "configuration": match,
                "region_names_on_this_instance": region_names,
            }

        region = asset.get_page_configuration(configuration_name, page_region)
        if region is None:
            raise errors.page_region_not_found_error(
                asset,
                configuration_name,
                page_region,
                region_names,
                context="cascade_get_page_config",
            )
        return {"configuration": match, "region": region.model_dump()}
    except ToolError:
        raise
    except Exception as exc:
        raise errors.unexpected_failure_error("cascade_get_page_config", exc) from exc


@mcp.tool()
def cascade_root_container_id(
    site_identifier: IdentifierType | CascadePath,
    asset_type: Literal["datadefinition", "sharedfield", "folder"],
) -> dict[str, Any]:
    """Get the root container id (top-level folder) for datadefinition/sharedfield/
    folder assets on a site - the starting point for browsing that asset type's
    tree from the top (e.g. the site's root Data Definitions folder)."""
    try:
        with _wrapper() as cascade:
            cascade.operations.read(site_identifier)
            result = errors.single_result(
                cascade.submit_requests(), context="cascade_root_container_id"
            )

        if isinstance(result, CascadeError | Exception):
            raise errors.read_asset_error(
                site_identifier,
                result,
                context="cascade_root_container_id",
                purpose="the site",
            )

        assert isinstance(result, Asset)
        if result.internal_type != "site":
            raise errors.not_a_site_error(result, context="cascade_root_container_id")

        container_id = result.root_container_id(cast(AssetTypes, asset_type))
        if container_id is None:
            raise errors.no_root_container_error(
                result, asset_type, context="cascade_root_container_id"
            )
        return {"container_id": container_id.hex}
    except ToolError:
        raise
    except Exception as exc:
        raise errors.unexpected_failure_error("cascade_root_container_id", exc) from exc


@mcp.tool()
def cascade_list_sites(limit: int | None = None) -> dict[str, Any]:
    """List every site on this Cascade server."""
    try:
        with _wrapper() as cascade:
            cascade.operations.listSites()
            result = errors.single_result(
                cascade.submit_requests(), context="cascade_list_sites"
            )

        if isinstance(result, CascadeError | Exception):
            raise errors.list_sites_failure_error(result)

        assert isinstance(result, ListElements)
        sites = [
            formatting.describe_element(e)
            for e in result.flat
            if isinstance(e, IdentifierType)
        ]
        return formatting.truncate_list(
            sites, key="sites", limit=limit or formatting.DEFAULT_LIST_LIMIT
        )
    except ToolError:
        raise
    except Exception as exc:
        raise errors.unexpected_failure_error("cascade_list_sites", exc) from exc


def main() -> None:
    load_environment_variables()  # fail fast, before starting the server
    mcp.run()  # stdio transport (default)


if __name__ == "__main__":
    main()
