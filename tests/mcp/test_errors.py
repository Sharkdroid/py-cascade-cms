import uuid

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from cascade_cms.cmstypes import CascadeError, IdentifierType
from cascade_cms.mcp.errors import (
    describe_identifier,
    read_asset_error,
    search_failure_error,
    single_result,
    unexpected_failure_error,
)


def test_single_result_empty_list_raises_tool_error():
    with pytest.raises(ToolError) as exc_info:
        single_result([], context="cascade_search")

    message = str(exc_info.value)
    assert "cascade_search" in message
    assert "CASCADE_URL" in message
    assert "CASCADE_API_KEY" in message


def test_single_result_returns_first_element():
    sentinel = object()

    assert single_result([sentinel], context="cascade_search") is sentinel


def test_describe_identifier_for_identifier_type():
    identifier = IdentifierType(identifier=uuid.uuid4(), asset_type="page")

    described = describe_identifier(identifier)

    assert described.startswith("page ")


def test_describe_identifier_for_path():
    path = {"path": "/a/b", "siteName": "my-site", "asset_type": "page"}

    described = describe_identifier(path)

    assert described == "page at my-site:/a/b"


def test_search_failure_error_uses_cascade_error_message():
    error = CascadeError(success=False, message="Site 'nope' not found")

    tool_error = search_failure_error(error)

    assert "Site 'nope' not found" in str(tool_error)
    assert "cascade_search" in str(tool_error)


def test_search_failure_error_falls_back_on_empty_message():
    error = CascadeError(success=False, message="")

    tool_error = search_failure_error(error)

    assert str(tool_error)  # never an empty/bare message


def test_search_failure_error_for_arbitrary_exception_gives_connectivity_hint():
    tool_error = search_failure_error(RuntimeError("boom"))

    message = str(tool_error)
    assert "CASCADE_URL" in message
    assert "CASCADE_API_KEY" in message


def test_read_asset_error_mentions_identifier_and_suggests_search():
    identifier = IdentifierType(identifier=uuid.uuid4(), asset_type="page")
    error = CascadeError(success=False, message="Asset not found")

    tool_error = read_asset_error(identifier, error)

    message = str(tool_error)
    assert "Asset not found" in message
    assert "cascade_search" in message
    assert "page" in message


def test_unexpected_failure_error_includes_tool_name():
    tool_error = unexpected_failure_error("cascade_read_asset", RuntimeError("boom"))

    message = str(tool_error)
    assert "cascade_read_asset" in message
    assert "boom" in message
