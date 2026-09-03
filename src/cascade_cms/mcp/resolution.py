"""Multi-hop read chains for the two schema-authoritative Phase 2 tools.

Deliberately separate from `references.py`: that module is a generic
display/discovery aid (does an `xxxId`/`xxxPath` pair *look* like a reference),
while this module does the two tools' *actual* resolution, using the specific,
confirmed field names directly (`asset._data.get("contentTypeId")`) rather than
routing through the generic detector - resolution correctness must not depend
on a display heuristic that requires a Path sibling to be present.
"""

from __future__ import annotations

from typing import Any, cast

from cascade_cms.cmstypes import Asset, AssetTypes, CascadeError, IdentifierType
from cascade_cms.wrapper import CascadeWrapperBase

from . import errors


def _read(
    cascade: CascadeWrapperBase,
    asset_id: str,
    asset_type: str,
    *,
    context: str,
    purpose: str,
) -> Asset:
    # asset_id/asset_type come from another asset's own _data (raw JSON strings,
    # not yet-validated types) - IdentifierType/pydantic validates and coerces
    # them at construction time; an unexpected value surfaces as a clear
    # ValidationError, caught by the tool handler's broad except and translated
    # to a ToolError, same as any other unforeseen failure.
    identifier = IdentifierType(
        identifier=cast(Any, asset_id), asset_type=cast(AssetTypes, asset_type)
    )
    cascade.operations.read(identifier)
    result = errors.single_result(cascade.submit_requests(), context=context)
    if isinstance(result, CascadeError | Exception):
        raise errors.read_asset_error(
            identifier, result, context=context, purpose=purpose
        )
    assert isinstance(result, Asset)
    return result


def resolve_content_type(
    asset: Asset, cascade: CascadeWrapperBase, *, context: str
) -> Asset:
    content_type_id = asset._data.get("contentTypeId")
    if not content_type_id:
        raise errors.no_resolvable_reference_error(
            asset, ["contentTypeId"], context=context
        )
    return _read(
        cascade,
        content_type_id,
        "contenttype",
        context=context,
        purpose="the content type",
    )


def resolve_data_definition(
    asset: Asset, cascade: CascadeWrapperBase, *, context: str
) -> Asset:
    # (a) direct field - not confirmed by any fixture seen; harmless no-op if absent.
    data_definition_id = asset._data.get("dataDefinitionId")
    if data_definition_id:
        return _read(
            cascade,
            data_definition_id,
            "datadefinition",
            context=context,
            purpose="the data definition",
        )
    # (b) via contentTypeId -> contentType.dataDefinitionId (confirmed by a real payload)
    content_type = resolve_content_type(asset, cascade, context=context)
    data_definition_id = content_type._data.get("dataDefinitionId")
    if not data_definition_id:
        raise errors.no_resolvable_reference_error(
            content_type, ["dataDefinitionId"], context=context
        )
    return _read(
        cascade,
        data_definition_id,
        "datadefinition",
        context=context,
        purpose="the data definition",
    )
