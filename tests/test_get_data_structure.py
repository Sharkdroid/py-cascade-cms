import pytest

from cascade_cms.cmstypes import Asset


def leaf(identifier: str, text: str = "") -> dict:
    return {"type": "text", "identifier": identifier, "text": text}


def group(identifier: str, *children: dict) -> dict:
    return {
        "type": "group",
        "identifier": identifier,
        "structuredDataNodes": list(children),
    }


def page(*nodes: dict) -> Asset:
    return Asset(
        {
            "asset": {
                "page": {
                    "id": "p1",
                    "structuredData": {
                        "structuredDataNodes": [
                            group("page-content", *nodes)
                        ]
                    },
                }
            }
        }
    )


def texts(nodes: list[dict] | None) -> list[str]:
    assert nodes is not None
    return [n["text"] for n in nodes]


def accordion_and_tabs() -> Asset:
    return page(
        group(
            "main-content",
            leaf("title", "own"),
            group(
                "accordion",
                leaf("show"),
                group("row", leaf("title", "row-1")),
                group("row", leaf("title", "row-2")),
                group("row", leaf("title", "row-3")),
            ),
            group("tabs", group("row", leaf("title", "tab-1"))),
        )
    )


def test_bare_group_matches_every_group_with_that_identifier():
    # Unchanged behavior: "row" is ambiguous between the
    # accordion's rows and the tabs row.
    found = accordion_and_tabs().get_data_structure("row", "title")
    assert texts(found) == ["row-1", "row-2", "row-3", "tab-1"]


def test_dotted_path_selects_one_parent():
    asset = accordion_and_tabs()
    found = asset.get_data_structure("accordion.row", "title")
    assert texts(found) == ["row-1", "row-2", "row-3"]
    found = asset.get_data_structure("tabs.row", "title")
    assert texts(found) == ["tab-1"]


def test_dotted_path_matches_end_of_chain_not_root():
    found = accordion_and_tabs().get_data_structure(
        "main-content.accordion.row", "title"
    )
    assert texts(found) == ["row-1", "row-2", "row-3"]


def test_dotted_path_that_does_not_exist_returns_none():
    asset = accordion_and_tabs()
    assert asset.get_data_structure("tabs.accordion", "title") is None
    assert asset.get_data_structure("accordion.tabs", "title") is None


def test_dotted_path_returns_nodes_by_reference():
    asset = accordion_and_tabs()
    nodes = asset.get_data_structure("accordion.row", "title")
    assert nodes is not None
    nodes[0]["text"] = "changed"
    again = asset.get_data_structure("accordion.row", "title")
    assert texts(again)[0] == "changed"


@pytest.mark.parametrize("bad", ["", ".row", "row.", "a..b"])
def test_invalid_group_path_raises(bad):
    with pytest.raises(ValueError):
        accordion_and_tabs().get_data_structure(bad, "title")


def test_default_search_can_return_a_nested_groups_field():
    # Pins the documented default: depth-first, so a group that
    # lacks the field can match one in a nested group.
    asset = page(
        group("column", group("video", leaf("title", "video")))
    )
    found = asset.get_data_structure("column", "title")
    assert texts(found) == ["video"]


def test_default_search_takes_the_first_match_in_document_order():
    asset = page(
        group(
            "column",
            group("video", leaf("title", "video")),
            leaf("title", "own"),
        )
    )
    assert texts(asset.get_data_structure("column", "title")) == [
        "video"
    ]


def test_direct_ignores_nested_groups():
    asset = page(
        group("column", group("video", leaf("title", "video")))
    )
    assert (
        asset.get_data_structure("column", "title", direct=True)
        is None
    )


def test_direct_returns_the_groups_own_field():
    asset = page(
        group(
            "column",
            group("video", leaf("title", "video")),
            leaf("title", "own"),
        )
    )
    found = asset.get_data_structure("column", "title", direct=True)
    assert texts(found) == ["own"]


def test_direct_skips_instances_without_the_field():
    asset = page(
        group("column", leaf("title", "a")),
        group("column", leaf("sub-title", "b")),
        group("column", leaf("title", "c")),
    )
    found = asset.get_data_structure("column", "title", direct=True)
    assert texts(found) == ["a", "c"]


def test_direct_does_not_match_a_group_identifier():
    asset = page(group("column", group("title")))
    assert (
        asset.get_data_structure("column", "title", direct=True)
        is None
    )


def test_direct_with_dotted_path_returns_one_node_per_row():
    found = accordion_and_tabs().get_data_structure(
        "accordion.row", "title", direct=True
    )
    assert texts(found) == ["row-1", "row-2", "row-3"]


def test_direct_on_main_content_returns_only_its_own_title():
    found = accordion_and_tabs().get_data_structure(
        "main-content", "title", direct=True
    )
    assert texts(found) == ["own"]


def test_miss_is_none_in_both_modes():
    asset = accordion_and_tabs()
    assert asset.get_data_structure("accordion", "nope") is None
    assert asset.get_data_structure("nope", "title") is None
    assert (
        asset.get_data_structure("nope", "title", direct=True)
        is None
    )


def test_group_without_structured_data_nodes_still_raises():
    asset = page({"type": "group", "identifier": "column"})
    with pytest.raises(KeyError):
        asset.get_data_structure("column", "title")
    with pytest.raises(KeyError):
        asset.get_data_structure("column", "title", direct=True)
