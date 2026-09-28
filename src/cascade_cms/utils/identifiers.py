"""Coerce raw Cascade ``{id, type, path}`` dicts into `IdentifierType`.

Pure data-shape helpers: no I/O, no requests.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import ValidationError

from cascade_cms.cmstypes import IdentifierType

_MAX_REASON = 120
_VALUE_ERROR_PREFIX = "Value error, "


def _reason(error: ValidationError) -> str:
    """Summarise a validation error as field names and reasons only.

    Input values are never included, and each reason is kept short.
    """
    parts: list[str] = []
    for detail in error.errors(include_input=False):
        loc = detail["loc"]
        name = ".".join(str(p) for p in loc) or "entry"
        if loc and loc[0] in ("type", "asset_type"):
            msg = "not a known asset type"
        elif loc and loc[0] == "path" and detail["type"] in (
            "model_type",
            "model_attributes_type",
        ):
            msg = "must be an object with 'path' and 'siteName'/'siteId'"
        else:
            msg = detail["msg"].removeprefix(_VALUE_ERROR_PREFIX)
        if len(msg) > _MAX_REASON:
            msg = msg[: _MAX_REASON - 3] + "..."
        parts.append(f"{name}: {msg}")
    return "; ".join(parts)


def to_identifier(raw: Mapping[str, Any]) -> IdentifierType:
    """Build an `IdentifierType` from a raw ``{id, type, ...}`` mapping.

    Accepts Cascade's own keys (``id``, ``type``) and the snake_case
    field names (``identifier``, ``asset_type``). Never filters by
    ``recycled``.

    Raises:
        ValueError: if ``raw`` is not a mapping or is malformed. The
            message names fields and reasons, never input values.
    """
    if not isinstance(raw, Mapping):
        # The contract is a single exception type (ValueError) for any bad entry.
        raise ValueError(f"expected a mapping, got {type(raw).__name__}")  # noqa: TRY004
    try:
        return IdentifierType(**raw)
    except ValidationError as err:
        raise ValueError(_reason(err)) from err


def to_identifiers(
    raw: Iterable[Mapping[str, Any]] | None,
    *,
    include_recycled: bool = False,
) -> list[IdentifierType]:
    """Build identifiers from raw entries such as a folder's ``children``.

    ``None`` gives ``[]``. Entries flagged ``recycled`` are dropped
    unless ``include_recycled`` is true. Order is preserved.

    Raises:
        ValueError: prefixed ``entry <index>: `` for a malformed entry.
    """
    if raw is None:
        return []
    found: list[IdentifierType] = []
    for index, entry in enumerate(raw):
        try:
            ident = to_identifier(entry)
        except ValueError as err:
            raise ValueError(f"entry {index}: {err}") from err
        if ident.recycled and not include_recycled:
            continue
        found.append(ident)
    return found
