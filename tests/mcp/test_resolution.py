import json
from pathlib import Path

import pytest
from conftest import SequentialFakeWrapper
from mcp.server.mcpserver.exceptions import ToolError

from cascade_cms.cmstypes import Asset
from cascade_cms.mcp import resolution

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> Asset:
    with open(FIXTURES / name) as f:
        return Asset(json.load(f))


@pytest.fixture
def content_type() -> Asset:
    return _load("contentType_resp.json")


@pytest.fixture
def data_definition() -> Asset:
    return _load("raw_data_def.json")


def test_resolve_content_type_success(content_type):
    asset = Asset({"asset": {"page": {"contentTypeId": content_type.get("id")}}})
    cascade = SequentialFakeWrapper([content_type])

    result = resolution.resolve_content_type(asset, cascade, context="test")

    assert result.get("id") == content_type.get("id")


def test_resolve_content_type_no_content_type_id_raises():
    asset = Asset({"asset": {"file": {"name": "x"}}})
    cascade = SequentialFakeWrapper([])

    with pytest.raises(ToolError, match="contentTypeId"):
        resolution.resolve_content_type(asset, cascade, context="test")


def test_resolve_data_definition_direct_field_short_circuits(data_definition):
    # Direct dataDefinitionId on the asset itself - no fixture confirms this
    # shape exists in practice, but the short-circuit must work if it does,
    # without needing a content-type hop at all.
    asset = Asset({"asset": {"block": {"dataDefinitionId": data_definition.get("id")}}})
    cascade = SequentialFakeWrapper([data_definition])

    result = resolution.resolve_data_definition(asset, cascade, context="test")

    assert result.get("id") == data_definition.get("id")


def test_resolve_data_definition_falls_back_to_content_type_chain(
    content_type, data_definition
):
    asset = Asset({"asset": {"page": {"contentTypeId": content_type.get("id")}}})
    cascade = SequentialFakeWrapper([content_type, data_definition])

    result = resolution.resolve_data_definition(asset, cascade, context="test")

    assert result.get("id") == data_definition.get("id")


def test_resolve_data_definition_no_reference_at_all_raises():
    asset = Asset({"asset": {"file": {"name": "x"}}})
    cascade = SequentialFakeWrapper([])

    with pytest.raises(ToolError, match="contentTypeId"):
        resolution.resolve_data_definition(asset, cascade, context="test")


def test_resolve_data_definition_content_type_missing_data_definition_id_raises(
    content_type,
):
    # content_type asset has no dataDefinitionId of its own - build a variant
    # without it to exercise the second failure branch.
    stripped = Asset({"asset": {"contentType": {**content_type._data}}})
    del stripped._data["dataDefinitionId"]
    asset = Asset({"asset": {"page": {"contentTypeId": stripped.get("id")}}})
    cascade = SequentialFakeWrapper([stripped])

    with pytest.raises(ToolError, match="dataDefinitionId"):
        resolution.resolve_data_definition(asset, cascade, context="test")


def test_resolve_propagates_cascade_error_as_tool_error(content_type):
    from cascade_cms.cmstypes import CascadeError

    asset = Asset({"asset": {"page": {"contentTypeId": content_type.get("id")}}})
    cascade = SequentialFakeWrapper([CascadeError(success=False, message="not found")])

    with pytest.raises(ToolError, match="not found"):
        resolution.resolve_content_type(asset, cascade, context="test")
