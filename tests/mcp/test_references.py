import json
from pathlib import Path

from cascade_cms.mcp.references import (
    _KNOWN_ASSET_TYPES,
    find_references,
    guess_asset_type,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_known_asset_types_guard():
    # Guards against AssetTypes.__value__/get_args() introspection breaking
    # silently on a future Python/typing change - fail loudly here instead.
    assert len(_KNOWN_ASSET_TYPES) == 57
    for expected in (
        "contenttype",
        "datadefinition",
        "pageconfigurationset",
        "metadataset",
        "folder",
        "site",
    ):
        assert expected in _KNOWN_ASSET_TYPES


def test_guess_asset_type_direct_match():
    assert guess_asset_type("contentType") == "contenttype"
    assert guess_asset_type("dataDefinition") == "datadefinition"
    assert guess_asset_type("pageConfigurationSet") == "pageconfigurationset"
    assert guess_asset_type("metadataSet") == "metadataset"
    assert guess_asset_type("site") == "site"


def test_guess_asset_type_alias():
    assert guess_asset_type("parentFolder") == "folder"


def test_guess_asset_type_unconfirmed_returns_none():
    assert guess_asset_type("parentContainer") is None


def test_find_references_top_level_only():
    data = {
        "id": "abc",
        "contentTypeId": "xyz",
        "contentTypePath": "Standard Page",
        "nested": {"someId": "n1", "somePath": "nested, ignored"},
    }
    refs = find_references(data)
    assert set(refs) == {"contentType"}
    assert refs["contentType"] == {
        "id": "xyz",
        "path": "Standard Page",
        "name": None,
        "asset_type": "contenttype",
    }


def test_find_references_never_matches_bare_id():
    data = {"id": "abc", "path": "/a/b"}
    assert find_references(data) == {}


def test_find_references_requires_path_or_name_sibling():
    data = {"orphanId": "no-sibling"}
    assert find_references(data) == {}


def test_find_references_name_sibling():
    data = {"siteId": "s1", "siteName": "www.example.com"}
    refs = find_references(data)
    assert refs["site"] == {
        "id": "s1",
        "path": None,
        "name": "www.example.com",
        "asset_type": "site",
    }


def test_find_references_against_real_content_type_payload():
    with open(FIXTURES / "contentType_resp.json") as f:
        data = json.load(f)["asset"]["contentType"]

    refs = find_references(data)

    assert set(refs) == {
        "pageConfigurationSet",
        "dataDefinition",
        "metadataSet",
        "parentContainer",
        "site",
    }
    assert refs["dataDefinition"]["asset_type"] == "datadefinition"
    assert refs["pageConfigurationSet"]["asset_type"] == "pageconfigurationset"
    assert refs["metadataSet"]["asset_type"] == "metadataset"
    assert refs["site"]["asset_type"] == "site"
    assert refs["site"]["name"] == "www.csi.edu"
    # Ambiguous - correctly left unguessed rather than guessed wrong.
    assert refs["parentContainer"]["asset_type"] is None
