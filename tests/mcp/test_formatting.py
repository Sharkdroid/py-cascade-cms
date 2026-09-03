import uuid

from cascade_cms.cmstypes import Asset, IdentifierType, ListElements
from cascade_cms.mcp.formatting import (
    format_asset,
    format_names_for_message,
    format_search_results,
    name_from_path,
    sort_by_relevance,
    truncate_list,
)


def _asset(data: dict) -> Asset:
    return Asset({"asset": {"page": data}})


def test_format_asset_concise_passes_scalars_through():
    asset = _asset(
        {
            "id": "abc123",
            "name": "My Page",
            "isPublishable": True,
            "note": None,
            "priority": 3,
        }
    )

    result = format_asset(asset, format="concise")

    assert result == {
        "id": "abc123",
        "name": "My Page",
        "isPublishable": True,
        "note": None,
        "priority": 3,
    }


def test_format_asset_concise_collapses_dict_and_list():
    asset = _asset(
        {
            "metadata": {"title": "x", "summary": "y"},
            "tags": ["a", "b", "c"],
        }
    )

    result = format_asset(asset, format="concise")

    assert result["metadata"] == {
        "_collapsed": True,
        "count": 2,
        "expand_with": 'cascade_read_asset(format="detailed")',
    }
    assert result["tags"] == {
        "_collapsed": True,
        "count": 3,
        "expand_with": 'cascade_read_asset(format="detailed")',
    }


def test_format_asset_concise_routes_structured_data_hint():
    asset = _asset({"structuredData": {"identifier": "root", "type": "group"}})

    result = format_asset(asset, format="concise")

    assert result["structuredData"]["expand_with"] == "cascade_get_data_structure"


def test_format_asset_concise_routes_page_configurations_hint():
    asset = _asset({"pageConfigurations": [{"name": "Default", "pageRegions": []}]})

    result = format_asset(asset, format="concise")

    assert result["pageConfigurations"]["expand_with"] == "cascade_get_page_config"
    assert result["pageConfigurations"]["count"] == 1


def test_format_asset_concise_never_leaves_uncollapsed_dict_or_list():
    asset = _asset(
        {
            "id": "abc",
            "metadata": {"a": 1},
            "tags": [1, 2],
            "structuredData": {"x": 1},
            "pageConfigurations": [],
        }
    )

    result = format_asset(asset, format="concise")

    for value in result.values():
        assert not isinstance(value, (dict, list)) or value.get("_collapsed") is True


def test_format_asset_concise_adds_references_when_present():
    asset = _asset({"contentTypeId": "abc", "contentTypePath": "Standard Page"})

    result = format_asset(asset, format="concise")

    assert result["_references"] == {
        "contentType": {
            "id": "abc",
            "path": "Standard Page",
            "name": None,
            "asset_type": "contenttype",
        }
    }


def test_format_asset_concise_omits_references_when_absent():
    asset = _asset({"id": "abc123", "name": "My Page"})

    result = format_asset(asset, format="concise")

    assert "_references" not in result


def test_format_asset_detailed_returns_raw_data_unmodified():
    raw = {"id": "abc", "metadata": {"a": 1}, "tags": [1, 2]}
    asset = _asset(raw)

    result = format_asset(asset, format="detailed")

    assert result == raw
    assert result is not raw  # dict(...) copy, not the same object
    assert result["metadata"] is raw["metadata"]  # nested values still by reference


def test_name_from_path_normal():
    assert name_from_path("/folder/subfolder/my-page") == "my-page"


def test_name_from_path_trailing_slash():
    assert name_from_path("/folder/subfolder/my-page/") == "my-page"


def test_name_from_path_root():
    assert name_from_path("/") is None


def test_name_from_path_empty_or_none():
    assert name_from_path("") is None
    assert name_from_path(None) is None


def _identifier(path: str, site: str = "example-site") -> IdentifierType:
    return IdentifierType(
        identifier=uuid.uuid4(),
        asset_type="page",
        path={"path": path, "siteName": site},
    )


def test_format_search_results_under_limit_has_more_false():
    elements = ListElements.model_validate(
        {"matches": [_identifier("/a/b").model_dump(by_alias=True)]}
    )

    result = format_search_results(elements, limit=20)

    assert result["total_count"] == 1
    assert result["has_more"] is False
    assert len(result["results"]) == 1
    assert result["results"][0]["name"] == "b"
    assert result["results"][0]["site"] == "example-site"


def test_format_search_results_truncates_and_sets_has_more():
    raw = [_identifier(f"/a/{i}").model_dump(by_alias=True) for i in range(5)]
    elements = ListElements.model_validate({"matches": raw})

    result = format_search_results(elements, limit=3)

    assert result["total_count"] == 5
    assert result["has_more"] is True
    assert len(result["results"]) == 3


def test_truncate_list_under_limit_passes_through_unchanged():
    result = truncate_list(["a", "b"], key="items", limit=5)

    assert result == {"items": ["a", "b"], "total_count": 2, "has_more": False}
    assert "expand_with" not in result


def test_truncate_list_over_limit_truncates_and_flags_has_more():
    result = truncate_list(["a", "b", "c", "d"], key="items", limit=2)

    assert result["items"] == ["a", "b"]
    assert result["total_count"] == 4
    assert result["has_more"] is True


def test_truncate_list_expand_with_only_present_when_truncated():
    under = truncate_list(["a"], key="items", limit=5, expand_with="hint")
    over = truncate_list(["a", "b"], key="items", limit=1, expand_with="hint")

    assert "expand_with" not in under
    assert over["expand_with"] == "hint"


def test_sort_by_relevance_matches_first_then_alphabetical():
    names = ["zebra", "post_details", "article", "post-office"]

    result = sort_by_relevance(names, "post")

    assert result == ["post-office", "post_details", "article", "zebra"]


def test_sort_by_relevance_case_insensitive():
    assert sort_by_relevance(["Article", "Zebra"], "art") == ["Article", "Zebra"]


def test_sort_by_relevance_no_match_falls_back_to_alphabetical():
    assert sort_by_relevance(["zebra", "article"], "nope") == ["article", "zebra"]


def test_format_names_for_message_under_limit():
    assert format_names_for_message(["a", "b"], limit=5) == "a, b"


def test_format_names_for_message_over_limit_appends_count():
    result = format_names_for_message(["a", "b", "c"], limit=2)

    assert result == "a, b, and 1 more"


def test_format_names_for_message_empty_list():
    assert format_names_for_message([]) == ""
