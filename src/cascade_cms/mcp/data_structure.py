"""Parse a Cascade data-definition's `xml` field (a `system-data-structure`
document) into a generic, schema-authoritative tree, and locate groups/fields
within it.

The element vocabulary observed across three real data-definition documents
(group, text, asset, shared-field, radio-item, dropdown-item - confirmed via
direct inspection, not assumed) is broad and open-ended: 15+ distinct attribute
names across those six tags. Rather than hand-model every possible Cascade field
type/widget (risking a wrong guess for a shape not yet seen), every element is
represented uniformly as `{"tag": ..., "attributes": {...}, "children": [...]}`,
losing no information. `group` elements are containers (children are more schema
nodes); any other tag is a leaf field (children, if present, are `radio-item`/
`dropdown-item` option entries, not further schema nodes).
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from typing import Any


def build_tree(xml_text: str) -> dict[str, Any]:
    """Parse a `system-data-structure` XML document into the generic tree shape.

    Uses `iterparse(events=("start", "end"))` with an explicit stack, clearing
    each element on its "end" event - the standard memory-bounded iterparse
    idiom, avoiding a full DOM build for a large document. Comments (there are
    several in real data definitions, e.g. `<!--<group ...>-->`) are never
    emitted with just ("start", "end") in the events tuple, so no special
    handling is needed to skip them.
    """
    stack: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    root: dict[str, Any] | None = None
    for event, elem in ET.iterparse(io.StringIO(xml_text), events=("start", "end")):
        if event == "start":
            node: dict[str, Any] = {"tag": elem.tag}
            stack.append((node, []))
        else:
            node, children = stack.pop()
            if elem.attrib:
                node["attributes"] = dict(elem.attrib)
            if children:
                node["children"] = children
            if stack:
                stack[-1][1].append(node)
            else:
                root = node
            elem.clear()
    assert root is not None
    return root


def _identifier(node: dict[str, Any]) -> str | None:
    return node.get("attributes", {}).get("identifier")


def find_group(tree: dict[str, Any], group_identifier: str) -> dict[str, Any] | None:
    """Recursive descent for the first `group` node whose `identifier` matches."""
    if tree.get("tag") == "group" and _identifier(tree) == group_identifier:
        return tree
    for child in tree.get("children", []):
        found = find_group(child, group_identifier)
        if found is not None:
            return found
    return None


def find_node(
    group_node: dict[str, Any], node_identifier: str
) -> dict[str, Any] | None:
    """DFS within a matched group's children for a leaf field (any non-`group`
    tag) whose `identifier` matches, descending into nested `group` children
    (never itself a match - `node_identifier` names a field, not a group)."""
    for child in group_node.get("children", []):
        if child.get("tag") != "group" and _identifier(child) == node_identifier:
            return child
        if child.get("tag") == "group":
            found = find_node(child, node_identifier)
            if found is not None:
                return found
    return None


def list_children(group_node: dict[str, Any]) -> list[dict[str, Any]]:
    """Immediate children of a matched group, as a compact directory listing -
    sorted alphabetically by identifier for a scannable, deterministic order
    (not raw XML tree-walk order)."""
    entries = [
        {
            "tag": child.get("tag"),
            "identifier": _identifier(child),
            "label": child.get("attributes", {}).get("label"),
            "type": child.get("attributes", {}).get("type"),
        }
        for child in group_node.get("children", [])
    ]
    return sorted(entries, key=lambda e: e["identifier"] or "")


def collect_group_identifiers(tree: dict[str, Any]) -> list[str]:
    """Every group identifier anywhere in the tree, sorted alphabetically.
    Uncapped - truncation for display is applied at the presentation layer,
    not baked into this walk, so `total_count` stays truthful."""
    identifiers: list[str] = []

    def _walk(node: dict[str, Any]) -> None:
        if node.get("tag") == "group":
            identifier = _identifier(node)
            if identifier:
                identifiers.append(identifier)
        for child in node.get("children", []):
            _walk(child)

    _walk(tree)
    return sorted(identifiers)
