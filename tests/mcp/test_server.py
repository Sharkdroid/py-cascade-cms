import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from cascade_cms.cmstypes import Asset, CascadeError, IdentifierType, ListElements
from cascade_cms.mcp import server

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeWrapper:
    """Stands in for CascadeWrapperBase: registers operations (ignored) and
    returns a pre-baked submit_requests() result, mirroring the real
    context-manager shape."""

    def __init__(self, submit_result):
        self.operations = MagicMock()
        self._submit_result = submit_result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def submit_requests(self, *args, **kwargs):
        return self._submit_result


@pytest.fixture
def patch_wrapper(monkeypatch):
    def _patch(submit_result):
        monkeypatch.setattr(server, "_wrapper", lambda: _FakeWrapper(submit_result))

    return _patch


def _identifier() -> IdentifierType:
    return IdentifierType(
        identifier=uuid.uuid4(),
        asset_type="page",
        path={"path": "/a/my-page", "siteName": "example-site"},
    )


def test_cascade_search_returns_formatted_results(patch_wrapper):
    elements = ListElements.model_validate(
        {"matches": [_identifier().model_dump(by_alias=True)]}
    )
    patch_wrapper([elements])

    result = server.cascade_search(query="foo", site="example-site")

    assert result["total_count"] == 1
    assert result["results"][0]["site"] == "example-site"
    assert result["results"][0]["name"] == "my-page"


def test_cascade_search_raises_tool_error_on_cascade_error(patch_wrapper):
    patch_wrapper([CascadeError(success=False, message="Site not found")])

    with pytest.raises(ToolError) as exc_info:
        server.cascade_search(query="foo", site="nope")

    assert "Site not found" in str(exc_info.value)


def test_cascade_search_raises_tool_error_on_empty_submit_result(patch_wrapper):
    patch_wrapper([])

    with pytest.raises(ToolError):
        server.cascade_search(query="foo", site="example-site")


def test_cascade_read_asset_concise_never_returns_uncollapsed_container(patch_wrapper):
    asset = Asset(
        {
            "asset": {
                "page": {
                    "id": "abc123",
                    "name": "My Page",
                    "metadata": {"title": "hi"},
                }
            }
        }
    )
    patch_wrapper([asset])

    result = server.cascade_read_asset(identifier=_identifier())

    assert result["id"] == "abc123"
    assert result["metadata"]["_collapsed"] is True


def test_cascade_read_asset_detailed_returns_raw_payload(patch_wrapper):
    raw = {"id": "abc123", "metadata": {"title": "hi"}}
    asset = Asset({"asset": {"page": raw}})
    patch_wrapper([asset])

    result = server.cascade_read_asset(identifier=_identifier(), format="detailed")

    assert result == raw


def test_cascade_read_asset_raises_tool_error_on_cascade_error(patch_wrapper):
    patch_wrapper([CascadeError(success=False, message="Asset not found")])

    with pytest.raises(ToolError) as exc_info:
        server.cascade_read_asset(identifier=_identifier())

    message = str(exc_info.value)
    assert "Asset not found" in message
    assert "cascade_search" in message


def test_cascade_read_asset_raises_tool_error_on_empty_submit_result(patch_wrapper):
    patch_wrapper([])

    with pytest.raises(ToolError):
        server.cascade_read_asset(identifier=_identifier())


def _load_asset(name: str) -> Asset:
    with open(FIXTURES / name) as f:
        return Asset(json.load(f))


@pytest.fixture
def content_type() -> Asset:
    return _load_asset("contentType_resp.json")


@pytest.fixture
def data_definition() -> Asset:
    return _load_asset("raw_data_def.json")


@pytest.fixture
def page(content_type) -> Asset:
    return Asset({"asset": {"page": {"contentTypeId": content_type.get("id")}}})


def test_cascade_get_data_structure_lists_children(
    patch_wrapper_sequence, page, content_type, data_definition
):
    patch_wrapper_sequence([page, content_type, data_definition])

    result = server.cascade_get_data_structure(
        identifier=_identifier(), group="right-column"
    )

    assert result["group"] == "right-column"
    assert result["data_definition"]["id"] == data_definition.get("id")
    assert [c["identifier"] for c in result["children"]] == ["display", "widget"]
    assert result["total_count"] == 2
    assert result["has_more"] is False
    assert "expand_with" not in result


def test_cascade_get_data_structure_returns_one_node(
    patch_wrapper_sequence, page, content_type, data_definition
):
    patch_wrapper_sequence([page, content_type, data_definition])

    result = server.cascade_get_data_structure(
        identifier=_identifier(), group="right-column", node_identifier="widget"
    )

    assert result["node"]["tag"] == "asset"
    assert result["node"]["attributes"]["identifier"] == "widget"


def test_cascade_get_data_structure_direct_data_definition_id_short_circuits(
    patch_wrapper_sequence, data_definition
):
    block = Asset({"asset": {"block": {"dataDefinitionId": data_definition.get("id")}}})
    patch_wrapper_sequence([block, data_definition])

    result = server.cascade_get_data_structure(
        identifier=_identifier(), group="right-column"
    )

    assert result["group"] == "right-column"


def test_cascade_get_data_structure_group_not_found(
    patch_wrapper_sequence, page, content_type, data_definition
):
    patch_wrapper_sequence([page, content_type, data_definition])

    with pytest.raises(ToolError) as exc_info:
        server.cascade_get_data_structure(identifier=_identifier(), group="nope")

    message = str(exc_info.value)
    assert "nope" in message
    assert "right-column" in message  # a real group name, present in the listing


def test_cascade_get_data_structure_node_not_found(
    patch_wrapper_sequence, page, content_type, data_definition
):
    patch_wrapper_sequence([page, content_type, data_definition])

    with pytest.raises(ToolError) as exc_info:
        server.cascade_get_data_structure(
            identifier=_identifier(), group="right-column", node_identifier="nope"
        )

    message = str(exc_info.value)
    assert "nope" in message
    assert "widget" in message


def test_cascade_get_data_structure_truncates_with_limit_and_expand_hint(
    patch_wrapper_sequence, page, content_type, data_definition
):
    patch_wrapper_sequence([page, content_type, data_definition])

    result = server.cascade_get_data_structure(
        identifier=_identifier(), group="right-column", limit=1
    )

    assert len(result["children"]) == 1
    assert result["has_more"] is True
    assert result["expand_with"].startswith("cascade_read_asset(")
    assert data_definition.get("id") in result["expand_with"]


def test_cascade_get_data_structure_no_resolvable_reference(patch_wrapper_sequence):
    orphan = Asset({"asset": {"file": {"name": "x"}}})
    patch_wrapper_sequence([orphan])

    with pytest.raises(ToolError, match="contentTypeId"):
        server.cascade_get_data_structure(identifier=_identifier(), group="anything")


def test_cascade_get_page_config_lists_configurations(
    patch_wrapper_sequence, page, content_type
):
    patch_wrapper_sequence([page, content_type])

    result = server.cascade_get_page_config(identifier=_identifier())

    assert result["content_type"]["name"] == "Standard Page"
    names = [c["name"] for c in result["configurations"]]
    assert names == ["ASPX", "XML"]
    assert result["total_count"] == 2
    assert result["has_more"] is False


def test_cascade_get_page_config_config_name_not_found(
    patch_wrapper_sequence, page, content_type
):
    patch_wrapper_sequence([page, content_type])

    with pytest.raises(ToolError) as exc_info:
        server.cascade_get_page_config(
            identifier=_identifier(), configuration_name="nope"
        )

    message = str(exc_info.value)
    assert "nope" in message
    assert "ASPX" in message


def test_cascade_get_page_config_region_names_come_from_instance_not_content_type(
    patch_wrapper_sequence, content_type
):
    page_with_config = Asset(
        {
            "asset": {
                "page": {
                    "contentTypeId": content_type.get("id"),
                    "pageConfigurations": [
                        {
                            "name": "ASPX",
                            "pageRegions": [{"name": "DEFAULT", "content": "hi"}],
                        }
                    ],
                }
            }
        }
    )
    patch_wrapper_sequence([page_with_config, content_type])

    result = server.cascade_get_page_config(
        identifier=_identifier(), configuration_name="ASPX"
    )

    assert result["region_names_on_this_instance"] == ["DEFAULT"]


def test_cascade_get_page_config_page_region_without_configuration_name_raises(
    patch_wrapper_sequence,
):
    with pytest.raises(ToolError, match="configuration_name"):
        server.cascade_get_page_config(identifier=_identifier(), page_region="DEFAULT")


def test_cascade_get_page_config_page_region_returns_content(
    patch_wrapper_sequence, content_type
):
    page_with_config = Asset(
        {
            "asset": {
                "page": {
                    "contentTypeId": content_type.get("id"),
                    "pageConfigurations": [
                        {
                            "name": "ASPX",
                            "pageRegions": [{"name": "DEFAULT", "content": "hello"}],
                        }
                    ],
                }
            }
        }
    )
    patch_wrapper_sequence([page_with_config, content_type])

    result = server.cascade_get_page_config(
        identifier=_identifier(), configuration_name="ASPX", page_region="DEFAULT"
    )

    assert result["region"]["content"] == "hello"


def test_cascade_get_page_config_page_region_not_authored_on_instance(
    patch_wrapper_sequence, content_type
):
    page_with_config = Asset(
        {
            "asset": {
                "page": {
                    "contentTypeId": content_type.get("id"),
                    "pageConfigurations": [{"name": "ASPX", "pageRegions": []}],
                }
            }
        }
    )
    patch_wrapper_sequence([page_with_config, content_type])

    with pytest.raises(ToolError, match="never authored"):
        server.cascade_get_page_config(
            identifier=_identifier(), configuration_name="ASPX", page_region="FOOTER"
        )


def _site_asset(**overrides) -> Asset:
    data = {
        "id": uuid.uuid4().hex,
        "name": "example-site",
        "rootDataDefinitionContainerId": uuid.uuid4().hex,
        "rootSharedFieldContainerId": uuid.uuid4().hex,
        "rootFolderId": uuid.uuid4().hex,
    }
    data.update(overrides)
    return Asset({"asset": {"site": data}})


def test_cascade_root_container_id_returns_hex_id(patch_wrapper):
    site = _site_asset()
    patch_wrapper([site])

    result = server.cascade_root_container_id(
        site_identifier=_identifier(), asset_type="folder"
    )

    assert result == {"container_id": site.get("rootFolderId")}


def test_cascade_root_container_id_not_a_site_raises(patch_wrapper):
    page = Asset({"asset": {"page": {"id": uuid.uuid4().hex, "name": "not-a-site"}}})
    patch_wrapper([page])

    with pytest.raises(ToolError, match="expected a site asset"):
        server.cascade_root_container_id(
            site_identifier=_identifier(), asset_type="folder"
        )


def test_cascade_root_container_id_unmapped_type_raises(patch_wrapper):
    site = _site_asset()
    del site._data["rootFolderId"]
    patch_wrapper([site])

    with pytest.raises(ToolError, match="folder"):
        server.cascade_root_container_id(
            site_identifier=_identifier(), asset_type="folder"
        )


def test_cascade_root_container_id_raises_tool_error_on_cascade_error(patch_wrapper):
    patch_wrapper([CascadeError(success=False, message="Site not found")])

    with pytest.raises(ToolError, match="Site not found"):
        server.cascade_root_container_id(
            site_identifier=_identifier(), asset_type="folder"
        )


def test_cascade_list_sites_returns_formatted_sites(patch_wrapper):
    elements = ListElements.model_validate(
        {
            "sites": [
                IdentifierType(
                    identifier=uuid.uuid4(), asset_type="site", path={"path": ""}
                ).model_dump(by_alias=True)
            ]
        }
    )
    patch_wrapper([elements])

    result = server.cascade_list_sites()

    assert result["total_count"] == 1
    assert result["has_more"] is False
    assert result["sites"][0]["type"] == "site"


def test_cascade_list_sites_truncates_with_limit(patch_wrapper):
    raw = [
        IdentifierType(
            identifier=uuid.uuid4(), asset_type="site", path={"path": ""}
        ).model_dump(by_alias=True)
        for _ in range(5)
    ]
    elements = ListElements.model_validate({"sites": raw})
    patch_wrapper([elements])

    result = server.cascade_list_sites(limit=2)

    assert result["total_count"] == 5
    assert result["has_more"] is True
    assert len(result["sites"]) == 2


def test_cascade_list_sites_raises_tool_error_on_cascade_error(patch_wrapper):
    patch_wrapper([CascadeError(success=False, message="listSites unavailable")])

    with pytest.raises(ToolError, match="listSites unavailable"):
        server.cascade_list_sites()
