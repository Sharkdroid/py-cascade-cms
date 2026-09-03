import json
from pathlib import Path

from cascade_cms.mcp.data_structure import (
    build_tree,
    collect_group_identifiers,
    find_group,
    find_node,
    list_children,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _real_xml() -> str:
    with open(FIXTURES / "raw_data_def.json") as f:
        return json.load(f)["asset"]["dataDefinition"]["xml"]


def test_build_tree_root_and_no_comments_leak_through():
    xml_text = """<system-data-structure>
        <!-- a commented-out field, must never appear -->
        <group identifier="g1" label="Group One">
            <text identifier="f1" label="Field One" required="true"/>
        </group>
    </system-data-structure>"""

    tree = build_tree(xml_text)

    assert tree["tag"] == "system-data-structure"
    assert len(tree["children"]) == 1
    group = tree["children"][0]
    assert group["tag"] == "group"
    assert group["attributes"]["identifier"] == "g1"
    field = group["children"][0]
    assert field == {
        "tag": "text",
        "attributes": {"identifier": "f1", "label": "Field One", "required": "true"},
    }


def test_build_tree_radio_items_become_generic_children():
    xml_text = """<system-data-structure>
        <group identifier="g1" label="Group One">
            <text identifier="choice" type="radiobutton">
                <radio-item value="Yes" show-fields="g1/other"/>
                <radio-item value="No"/>
            </text>
        </group>
    </system-data-structure>"""

    tree = build_tree(xml_text)
    field = tree["children"][0]["children"][0]

    assert field["tag"] == "text"
    assert len(field["children"]) == 2
    assert field["children"][0] == {
        "tag": "radio-item",
        "attributes": {"value": "Yes", "show-fields": "g1/other"},
    }


def test_against_real_data_definition_xml():
    tree = build_tree(_real_xml())

    assert tree["tag"] == "system-data-structure"
    groups = collect_group_identifiers(tree)
    assert groups == sorted(groups)  # alphabetical
    assert set(groups) == {
        "page-content",
        "left-column",
        "right-column",
        "post_details",
        "homePageOptions",
        "content",
        "hero",
        "article",
    }

    right_column = find_group(tree, "right-column")
    assert right_column is not None
    children = list_children(right_column)
    assert [c["identifier"] for c in children] == ["display", "widget"]  # alphabetical
    assert children[1]["tag"] == "asset"
    assert children[1]["type"] == "page"

    widget = find_node(right_column, "widget")
    assert widget is not None
    assert widget["tag"] == "asset"
    assert widget["attributes"]["restrict-to-folder"] == "/_widgets"


def test_find_group_not_found_returns_none():
    tree = build_tree(_real_xml())
    assert find_group(tree, "does-not-exist") is None


def test_find_node_not_found_returns_none():
    tree = build_tree(_real_xml())
    group = find_group(tree, "right-column")
    assert find_node(group, "does-not-exist") is None


def test_find_node_descends_into_nested_groups():
    # "post_details" has a nested group "homePageOptions" containing the leaf
    # field "image" - find_node must descend past post_details' own direct
    # children (none named "image") into the nested group to find it.
    tree = build_tree(_real_xml())
    post_details = find_group(tree, "post_details")
    direct_identifiers = [c["identifier"] for c in list_children(post_details)]
    assert "homePageOptions" in direct_identifiers
    assert "image" not in direct_identifiers
    node = find_node(post_details, "image")
    assert node is not None
    assert node["tag"] == "asset"
    assert node["attributes"]["restrict-to-folder"] == "/_files/images/homepage"
