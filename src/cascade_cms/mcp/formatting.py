"""Concise/detailed asset formatting and search-result shaping.

Reaches into `Asset._data` directly (a leading-underscore attribute) - Asset
has no public bulk-iteration API, and the library's own
`edit_log_identifier_from_asset()` (cmstypes.py) already does the same thing,
so this is precedented, not a hack.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from cascade_cms.cmstypes import Asset, IdentifierType, ListElements

from .references import find_references

# Locked routing rule (MCP_IMPLEMENTATION_PLAN_REV.md §6.2). "structuredData"
# is Cascade's documented REST field name for data-bound asset types, but is
# NOT yet confirmed against a real payload in this repo (no fixture
# references it) - unlike "pageConfigurations", which Asset.__init__ already
# parses. Confirm this key name against a real cascade_read_asset
# (format="detailed") response during Phase 1 smoke testing; if it turns out
# to be wrong, only that one hint's specificity is affected - it falls
# through to the generic detailed-mode hint below, which is still correct.
_EXPAND_HINT_BY_KEY: dict[str, str] = {
    "structuredData": "cascade_get_data_structure",
    "pageConfigurations": "cascade_get_page_config",
}
_DEFAULT_EXPAND_HINT = 'cascade_read_asset(format="detailed")'

_SCALAR_TYPES = (str, int, float, bool, type(None))


def _collapse_value(key: str, value: Any) -> Any:
    if isinstance(value, _SCALAR_TYPES):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dict, list)):
        return {
            "_collapsed": True,
            "count": len(value),
            "expand_with": _EXPAND_HINT_BY_KEY.get(key, _DEFAULT_EXPAND_HINT),
        }
    # Raw JSON only ever produces the types above; stringify rather than
    # crash on anything unforeseen.
    return str(value)


def format_asset(
    asset: Asset, *, format: Literal["concise", "detailed"]
) -> dict[str, Any]:
    if format == "detailed":
        return dict(asset._data)
    collapsed = {key: _collapse_value(key, value) for key, value in asset._data.items()}
    references = find_references(asset._data)
    if references:
        collapsed["_references"] = references
    return collapsed


def name_from_path(path: str | None) -> str | None:
    """Derive a pseudo display-name from an asset's path - Cascade search
    results carry no name/title field of their own."""
    if not path:
        return None
    segment = path.rstrip("/").rsplit("/", 1)[-1]
    return segment or None


def describe_element(element: IdentifierType) -> dict[str, Any]:
    return {
        "id": element.get_id,
        "type": element.get_type,
        "site": element.get_sitename,
        "path": element.get_path,
        "name": name_from_path(element.get_path),
    }


def format_search_results(elements: ListElements, *, limit: int) -> dict[str, Any]:
    """Cascade's search endpoint has no limit/page-size param
    (`SearchInformation` has no such field) and exposes no total-match-count
    either, so limiting and counting both happen client-side here:
      - total_count = number of elements Cascade actually returned in THIS
        response (NOT a true site-wide match count - Cascade doesn't expose one).
      - has_more = whether client-side truncation to `limit` actually dropped any.
    """
    identifiers = [e for e in elements.flat if isinstance(e, IdentifierType)]
    total_returned = len(identifiers)
    truncated = identifiers[:limit]
    return {
        "results": [describe_element(e) for e in truncated],
        "total_count": total_returned,
        "has_more": total_returned > limit,
    }


DEFAULT_LIST_LIMIT = 50


def truncate_list(
    items: list[Any],
    *,
    key: str,
    limit: int = DEFAULT_LIST_LIMIT,
    expand_with: str | None = None,
) -> dict[str, Any]:
    """Cap a list for LLM consumption, wrapped under `key` with the same
    total_count/has_more shape `format_search_results` already established.

    When truncated and `expand_with` is given, surfaces it as a hint - same
    convention as the collapsed-field expand_with hints in `_collapse_value` -
    so an agent that needs the full picture knows where to get it in one call
    rather than discovering the detailed-mode fallback on its own.
    """
    result: dict[str, Any] = {
        key: items[:limit],
        "total_count": len(items),
        "has_more": len(items) > limit,
    }
    if len(items) > limit and expand_with:
        result["expand_with"] = expand_with
    return result


def sort_by_relevance(names: list[str], guess: str) -> list[str]:
    """Names containing `guess` (case-insensitive) sort first, alphabetically
    among themselves; the rest follow, also alphabetically.

    Used for "not found, did you mean" listings, where the agent's own failed
    guess is a free relevance signal - concentrates the truncation-hides-the-
    target risk fix exactly where it matters (a failed lookup), rather than
    guessing at general-purpose relevance.
    """
    guess_lower = guess.lower()
    matches = sorted(n for n in names if guess_lower in n.lower())
    rest = sorted(n for n in names if guess_lower not in n.lower())
    return matches + rest


def format_names_for_message(
    names: list[str], *, limit: int = DEFAULT_LIST_LIMIT
) -> str:
    """Join names for embedding in a self-correcting ToolError message, capped."""
    shown = ", ".join(names[:limit])
    if len(names) > limit:
        shown += f", and {len(names) - limit} more"
    return shown
