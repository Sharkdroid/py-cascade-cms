"""Translate Cascade-level failures (CascadeError values, or exceptions that
leaked out of submit_requests) into self-correcting MCP ToolErrors. Every
message here names what was tried, what's actually available, and/or which
tool to call next - never a bare "not found" or raw traceback.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from cascade_cms.cmstypes import Asset, CascadeError, IdentifierType, Path

from .formatting import format_names_for_message, sort_by_relevance


def describe_identifier(identifier: IdentifierType | Path) -> str:
    if isinstance(identifier, IdentifierType):
        return f"{identifier.get_type} {identifier.get_id}"
    site = identifier.get("siteName")
    return f"{identifier['asset_type']} at {site}:{identifier['path']}"


def single_result(results: list[Any], *, context: str) -> Any:
    """Unwrap a one-chain `submit_requests()` result.

    A top-level `submit_requests()` failure returns `[]` (see
    `CascadeWrapperBase.submit_requests`), so this raises a ToolError instead
    of letting `results[0]` raise a bare IndexError.
    """
    if not results:
        raise ToolError(
            f"{context}: no response was received from Cascade - this usually means a "
            "connectivity or session-level failure rather than a bad request. Check "
            "CASCADE_URL and CASCADE_API_KEY, confirm the server is reachable, and retry."
        )
    return results[0]


def search_failure_error(result: CascadeError | Exception) -> ToolError:
    if isinstance(result, CascadeError):
        message = (
            result.message
            or "Cascade reported a search failure with no further detail."
        )
        return ToolError(f"cascade_search failed: {message}")
    return ToolError(
        f"cascade_search failed unexpectedly ({result}). This usually indicates a "
        "connectivity/credential problem rather than a bad query - check CASCADE_URL "
        "and CASCADE_API_KEY."
    )


def read_asset_error(
    identifier: IdentifierType | Path,
    result: CascadeError | Exception,
    *,
    context: str = "cascade_read_asset",
    purpose: str | None = None,
) -> ToolError:
    described = describe_identifier(identifier)
    target = f" ({purpose})" if purpose else ""
    if isinstance(result, CascadeError):
        message = result.message or "asset not found"
        return ToolError(
            f"{context} failed to read {described}{target}: {message}. "
            "Try cascade_search first to confirm the correct id/type/path, then retry "
            "cascade_read_asset with its result."
        )
    return ToolError(
        f"{context} failed unexpectedly reading {described}{target} ({result}). This usually "
        "indicates a connectivity/credential problem - check CASCADE_URL and CASCADE_API_KEY."
    )


def no_resolvable_reference_error(
    asset: Asset, tried_fields: list[str], *, context: str
) -> ToolError:
    tried = ", ".join(tried_fields)
    available = format_names_for_message(sorted(asset._data.keys())) or "(none)"
    return ToolError(
        f"{context}: could not resolve a reference from this asset - tried field(s) {tried}, "
        f"none present. Fields actually on this asset: {available}. Try "
        'cascade_read_asset(format="detailed") to inspect it directly.'
    )


def no_xml_field_error(data_definition: Asset, *, context: str) -> ToolError:
    return ToolError(
        f"{context}: data definition {data_definition.get('id')} has no 'xml' field, so its "
        'schema can\'t be parsed. Try cascade_read_asset(format="detailed") on it directly to '
        "inspect what it actually contains."
    )


def group_not_found_error(
    data_definition: Asset, available_groups: list[str], group: str, *, context: str
) -> ToolError:
    available = (
        format_names_for_message(sort_by_relevance(available_groups, group)) or "(none)"
    )
    return ToolError(
        f"{context}: group '{group}' not found in data definition "
        f"{data_definition.get('id')}. Available groups: {available}."
    )


def node_not_found_error(
    data_definition: Asset,
    group: str,
    available_identifiers: list[str],
    node_identifier: str,
    *,
    context: str,
) -> ToolError:
    available = (
        format_names_for_message(
            sort_by_relevance(available_identifiers, node_identifier)
        )
        or "(none)"
    )
    return ToolError(
        f"{context}: field '{node_identifier}' not found in group '{group}' of data definition "
        f"{data_definition.get('id')}. Available fields in this group: {available}."
    )


def config_name_not_found_error(
    content_type: Asset,
    available_names: list[str],
    configuration_name: str,
    *,
    context: str,
) -> ToolError:
    available = (
        format_names_for_message(sort_by_relevance(available_names, configuration_name))
        or "(none)"
    )
    return ToolError(
        f"{context}: page configuration '{configuration_name}' not found for content type "
        f"{content_type.get('name')}. Available configurations: {available}."
    )


def page_region_not_found_error(
    asset: Asset,
    configuration_name: str,
    page_region: str,
    available_regions: list[str],
    *,
    context: str,
) -> ToolError:
    available = (
        format_names_for_message(sort_by_relevance(available_regions, page_region))
        or "(none)"
    )
    return ToolError(
        f"{context}: region '{page_region}' not found in configuration '{configuration_name}' "
        f"on this asset instance. This can mean the configuration name is valid but this "
        f"particular asset was never authored with content for this region, not that the name "
        f"is wrong. Regions present on this instance: {available}."
    )


def page_region_requires_configuration_name_error(*, context: str) -> ToolError:
    return ToolError(
        f"{context}: page_region was given without configuration_name - a region only makes "
        "sense within a specific configuration. Supply configuration_name too, or omit "
        "page_region to list available configurations first."
    )


def not_a_site_error(asset: Asset, *, context: str) -> ToolError:
    return ToolError(
        f"{context}: expected a site asset, but read a '{asset.internal_type}' asset "
        f"({asset.get('name')!r}) instead. Root container ids only exist on site assets - "
        "pass the identifier/path of the site itself."
    )


def no_root_container_error(site: Asset, asset_type: str, *, context: str) -> ToolError:
    available = format_names_for_message(sorted(Asset._ROOT_CONTAINER_FIELDS.keys()))
    return ToolError(
        f"{context}: '{asset_type}' has no known root container field on site "
        f"{site.get('name')!r}. Supported asset_type values: {available}."
    )


def list_sites_failure_error(result: CascadeError | Exception) -> ToolError:
    if isinstance(result, CascadeError):
        message = (
            result.message
            or "Cascade reported a listSites failure with no further detail."
        )
        return ToolError(f"cascade_list_sites failed: {message}")
    return ToolError(
        f"cascade_list_sites failed unexpectedly ({result}). This usually indicates a "
        "connectivity/credential problem - check CASCADE_URL and CASCADE_API_KEY."
    )


def unexpected_failure_error(tool_name: str, exc: Exception) -> ToolError:
    """Last-resort translation so no tool body can let a raw traceback leak."""
    return ToolError(f"{tool_name} failed unexpectedly: {exc}")
