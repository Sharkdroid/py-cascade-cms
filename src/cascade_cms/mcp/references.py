"""Generic, asset-type-agnostic detection of cross-asset references.

Cascade's REST payloads follow a naming convention: a field ending in `Id` that
points at another asset has a sibling field carrying a human-readable value -
usually `<prefix>Path` (e.g. `contentTypeId`/`contentTypePath`), sometimes
`<prefix>Name` (e.g. `siteId`/`siteName`). This is confirmed across multiple real
asset types (page, contenttype), not just one - so detection here is generic and
applies to any asset, not hardcoded to a specific relationship.

This is a *display/discovery* aid (surfaced via `formatting.format_asset`'s
`_references` key) - correctness of the actual multi-hop resolution chains in
`resolution.py` must not depend on this heuristic firing.
"""

from __future__ import annotations

from typing import Any, get_args

from cascade_cms.cmstypes import AssetTypes

_KNOWN_ASSET_TYPES: frozenset[str] = frozenset(get_args(AssetTypes.__value__))

# Prefixes that don't match a literal AssetTypes value directly but are
# confirmed (via a real payload) to mean a specific type. Only what's
# confirmed is listed here - mirrors Asset._ROOT_CONTAINER_FIELDS's
# "don't guess" discipline in cmstypes.py.
_REFERENCE_TYPE_ALIASES: dict[str, str] = {
    "parentfolder": "folder",
}


def guess_asset_type(prefix: str) -> str | None:
    """Guess the asset_type a `<prefix>Id` field refers to, or None if unconfirmed.

    `parentContainerId` deliberately returns None - parent containers can be
    folders or several other container types, and guessing wrong is worse than
    not guessing.
    """
    candidate = prefix.lower()
    if candidate in _KNOWN_ASSET_TYPES:
        return candidate
    return _REFERENCE_TYPE_ALIASES.get(candidate)


def find_references(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Scan `data`'s top-level keys for `<prefix>Id` fields with a sibling
    `<prefix>Path` or `<prefix>Name`, returning one entry per detected reference.

    Top-level only, mirroring `format_asset`'s existing top-level-only contract.
    Never matches an asset's own lowercase `"id"` field (it doesn't end in the
    capital-`"Id"` suffix this looks for).
    """
    references: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if not key.endswith("Id"):
            continue
        prefix = key[: -len("Id")]
        if not prefix:
            continue
        path_key = prefix + "Path"
        name_key = prefix + "Name"
        if path_key not in data and name_key not in data:
            continue
        references[prefix] = {
            "id": value,
            "path": data.get(path_key),
            "name": data.get(name_key),
            "asset_type": guess_asset_type(prefix),
        }
    return references
