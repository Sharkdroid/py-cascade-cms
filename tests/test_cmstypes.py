import json
from uuid import UUID

import pytest

from cascade_cms.cmstypes import (
    Asset,
    AssetAdapter,
    IdentifierType,
    ListElements,
    Path,
    deleteParameters,
    moveParameters,
    resolve_identifier,
)


def test_resolve_identifier_from_identifier_type(page_identifier):
    assert resolve_identifier(page_identifier) == (
        "page",
        "8b320f55ac1001062545a6d2562cee4b",
    )


def test_resolve_identifier_from_path():
    path = Path(
        path="/cms/index",
        siteId=UUID("8b320f55ac1001062545a6d2562cee4b"),
        siteName="www.csi.edu",
        asset_type="page",
    )
    assert resolve_identifier(path) == ("page", "www.csi.edu", "/cms/index")


def test_resolve_identifier_path_requires_sitename():
    path = Path(
        path="/cms/index",
        siteId=UUID("8b320f55ac1001062545a6d2562cee4b"),
        asset_type="page",
    )
    with pytest.raises(ValueError):
        resolve_identifier(path)


def test_resolve_identifier_path_without_site_id():
    # PathBase.siteId is NotRequired: resolve_identifier never reads it.
    path = Path(
        path="/cms/index",
        siteName="www.csi.edu",
        asset_type="page",
    )
    assert resolve_identifier(path) == ("page", "www.csi.edu", "/cms/index")


def test_move_parameters_aliases_nested_identifier():
    destination = IdentifierType(
        identifier=UUID("8b320f55ac1001062545a6d2562cee4b"),
        asset_type="folder",
    )
    payload = moveParameters(
        destinations=[destination],
        do_workflow=False,
        destination_container_identifier=destination,
        new_name="",
        unpublish=False,
    )
    dumped = payload.model_dump()["moveParameters"]

    assert dumped["destinationContainerIdentifier"] == {
        "id": destination.identifier.hex,
        "type": "folder",
        "recycled": None,
        "path": None,
    }
    assert dumped["destinations"] == [
        {
            "id": destination.identifier.hex,
            "type": "folder",
            "recycled": None,
            "path": None,
        }
    ]


def test_delete_parameters_aliases_nested_identifiers():
    destination = IdentifierType(
        identifier=UUID("8b320f55ac1001062545a6d2562cee4b"),
        asset_type="folder",
    )
    payload = deleteParameters(
        do_workflow=False,
        destinations_identifiers=[destination],
        unpublish=False,
    )
    dumped = payload.model_dump()["deleteParameters"]

    assert dumped["destinations"] == [
        {
            "id": destination.identifier.hex,
            "type": "folder",
            "recycled": None,
            "path": None,
        }
    ]


def _raw_asset(**page_config_extra):
    return {
        "asset": {
            "page": {
                "id": "8b320f55ac1001062545a6d2562cee4b",
                "name": "index",
                "pageConfigurations": [
                    {
                        "name": "Default",
                        "templateId": "aaaa0f55ac1001062545a6d2562cee4",
                        "blockId": "bbbb0f55ac1001062545a6d2562cee4",
                        "formatId": "cccc0f55ac1001062545a6d2562cee4",
                        "pageRegions": [
                            {"name": "DEFAULT", "content": "<p>hi</p>"},
                        ],
                        **page_config_extra,
                    }
                ],
            }
        }
    }


def test_asset_dump_json_preserves_page_configuration_bindings():
    asset = Asset(_raw_asset())
    dumped = json.loads(AssetAdapter().dump_json(asset))
    config = dumped["asset"]["page"]["pageConfigurations"][0]

    assert config["templateId"] == "aaaa0f55ac1001062545a6d2562cee4"
    assert config["blockId"] == "bbbb0f55ac1001062545a6d2562cee4"
    assert config["formatId"] == "cccc0f55ac1001062545a6d2562cee4"
    assert config["pageRegions"] == [{"name": "DEFAULT", "content": "<p>hi</p>"}]


def test_list_elements_parses_list_sites_response():
    payload = {
        "sites": [
            {"id": "8b320f55ac1001062545a6d2562cee4b", "type": "site"},
        ]
    }
    parsed = ListElements.model_validate(payload)
    assert len(parsed.elements) == 1
    assert parsed.elements[0].get_type == "site"


@pytest.mark.parametrize(
    ("raw_key", "expected"),
    [
        ("datadefinition", "datadefinition"),
        ("dataDefinition", "datadefinition"),
        ("sharedField", "sharedfield"),
        ("scriptFormat", "format"),
    ],
)
def test_asset_type_normalizes_response_key(raw_key, expected):
    asset = Asset({"asset": {raw_key: {"id": "x"}}})
    assert asset.asset_type == expected


def test_asset_root_container_id_known_and_unknown_types():
    site = Asset(
        {
            "asset": {
                "site": {
                    "rootDataDefinitionContainerId": "8b320f55ac1001062545a6d2562cee4b",
                    "rootFolderId": "8b320f55ac1001062545a6d2562cee4c",
                }
            }
        }
    )
    assert site.root_container_id("datadefinition") == UUID(
        "8b320f55ac1001062545a6d2562cee4b"
    )
    assert site.root_container_id("folder") == UUID(
        "8b320f55ac1001062545a6d2562cee4c"
    )
    assert site.root_container_id("sharedfield") is None
    assert site.root_container_id("template") is None
