"""Page configurations and regions are read-only snapshots."""

import json

import pytest
from pydantic import ValidationError
from test_wrapper import ID_ONE, StubDriver, make_asset, make_wrapper

from cascade_cms.cmstypes import (
    Asset,
    CascadeSuccess,
    IdentifierType,
    PageConfiguration,
    PageRegion,
    ReadOnlyPageConfigError,
    asset_adapter,
)

REGION = {
    "name": "ALERT-BANNER",
    "blockId": "32ff90a2ac1001066485f86178913ba8",
    "blockPath": "_common/_cms/blocks/alert banner",
    "blockRecycled": False,
    "noBlock": False,
    "formatId": "330384c3ac1001066485f86121a12634",
    "formatPath": "_common/_cms/formats/alert-banner",
    "formatRecycled": False,
    "noFormat": False,
    "id": "6e2e008aac10010f3e1865e77b6ebf1a",
}
CONFIG = {
    "name": "ASPX",
    "defaultConfiguration": True,
    "templateId": "fdbd80e9ac1001065466b17994c4e4a3",
    "templatePath": "_common/_cms/templates/standard-page",
    "formatRecycled": False,
    "pageRegions": [REGION],
    "includeXMLDeclaration": False,
    "publishable": False,
    "id": "ff9c4933ac1001065466b1795a679dce",
}


def _page() -> Asset:
    return make_asset(
        id=ID_ONE,
        pageConfigurations=[
            json.loads(json.dumps(CONFIG)),
            {"name": "XML", "pageRegions": []},
        ],
    )


def _payload(asset: Asset) -> dict:
    return json.loads(asset_adapter.dump_json(asset))["asset"]["page"]


def _config(asset: Asset, name: str = "ASPX") -> PageConfiguration:
    config = asset.get_page_configuration(name)
    assert isinstance(config, PageConfiguration)
    return config


def _region(asset: Asset) -> PageRegion:
    region = asset.get_page_configuration("ASPX", "ALERT-BANNER")
    assert isinstance(region, PageRegion)
    return region


def test_parses_full_shape():
    config = _config(_page())
    assert config.default_configuration is True
    assert config.template_path == "_common/_cms/templates/standard-page"
    assert config.include_xml_declaration is False
    assert config.publishable is False
    assert config.id == CONFIG["id"]
    region = config.page_regions[0]
    assert region.name == "ALERT-BANNER"
    assert region.block_id == REGION["blockId"]
    assert region.format_path == REGION["formatPath"]
    assert region.id == REGION["id"]


def test_empty_page_regions():
    assert _config(_page(), "XML").page_regions == ()


def test_missing_block_and_format_keys_are_none_not_trusted_flags():
    asset = make_asset(
        id=ID_ONE,
        pageConfigurations=[
            {"name": "ASPX", "pageRegions": [{"name": "R", "noBlock": False}]}
        ],
    )
    region = asset.get_page_configuration("ASPX", "R")
    assert isinstance(region, PageRegion)
    assert region.block_id is None and region.block_path is None
    assert region.no_block is False  # passed through as Cascade reported it
    assert region.no_format is None


@pytest.mark.parametrize("attr", ["name", "block_id", "no_block", "id"])
def test_region_assignment_raises_with_template_message(attr):
    region = _region(_page())
    with pytest.raises(ReadOnlyPageConfigError, match="template level") as exc:
        setattr(region, attr, "x")
    assert "`template` asset's `pageRegions`" in str(exc.value)


@pytest.mark.parametrize("attr", ["name", "publishable", "template_id"])
def test_config_assignment_raises_with_configuration_set_message(attr):
    config = _config(_page())
    with pytest.raises(ReadOnlyPageConfigError) as exc:
        setattr(config, attr, "x")
    assert "pageConfigurationSet" in str(exc.value)


def test_deletion_raises():
    asset = _page()
    with pytest.raises(ReadOnlyPageConfigError):
        del _region(asset).name
    with pytest.raises(ReadOnlyPageConfigError):
        del _config(asset).name


def test_read_only_error_is_attribute_error():
    assert issubclass(ReadOnlyPageConfigError, AttributeError)


def test_page_regions_is_immutable():
    config = _config(_page())
    assert isinstance(config.page_regions, tuple)
    with pytest.raises(AttributeError):
        config.page_regions.append(REGION)  # type: ignore[attr-defined]


def test_attempted_mutations_leave_payload_unchanged():
    asset = _page()
    before = _payload(asset)
    for target, attr in ((_region(asset), "block_id"), (_config(asset), "name")):
        with pytest.raises(ReadOnlyPageConfigError):
            setattr(target, attr, "changed")
    assert _payload(asset) == before
    config = before["pageConfigurations"][0]
    assert config["templateId"] == CONFIG["templateId"]
    assert config["pageRegions"][0]["blockId"] == REGION["blockId"]
    assert config["pageRegions"][0]["noBlock"] is False


def test_wholesale_reassignment_raises():
    asset = _page()
    with pytest.raises(ReadOnlyPageConfigError, match="pageConfigurationSet"):
        asset.pageConfigurations = []
    assert len(_payload(asset)["pageConfigurations"]) == 2


def test_raw_change_visible_on_next_lookup():
    asset = _page()
    asset._data["pageConfigurations"][0]["pageRegions"][0]["blockId"] = "b2"
    assert _region(asset).block_id == "b2"


def test_lookup_misses_return_none():
    asset = _page()
    assert asset.get_page_configuration("NOPE") is None
    assert asset.get_page_configuration("ASPX", "NOPE") is None


def test_page_configs_compat_list_of_page_configuration():
    asset = _page()
    assert isinstance(asset._page_configs, list)
    assert [c.name for c in asset._page_configs] == ["ASPX", "XML"]


def test_malformed_config_fails_at_creation():
    with pytest.raises(ValidationError):
        make_asset(id=ID_ONE, pageConfigurations=[{"pageRegions": []}])


def test_edit_chain_sends_regions_untouched():
    ident = IdentifierType(id=ID_ONE, type="page")
    driver = StubDriver([[_page()], [CascadeSuccess(success=True)]])
    wrapper = make_wrapper(driver)
    try:

        def attempt(asset: Asset) -> Asset:
            region = asset.get_page_configuration("ASPX", "ALERT-BANNER")
            assert isinstance(region, PageRegion)
            with pytest.raises(ReadOnlyPageConfigError):
                region.block_id = "changed"
            return asset

        wrapper.operations.read(ident).edit(attempt)
        results = wrapper.submit_requests(Asset)
        assert results.failed == []

        edits = [
            r
            for batch in driver.batches
            for r in batch
            if r.method == "POST" and r.url.endswith("/edit")
        ]
        assert len(edits) == 1
        sent = json.loads(asset_adapter.dump_json(edits[0].payload))["asset"]["page"]
        assert sent["pageConfigurations"][0] == CONFIG
    finally:
        driver.eventLoop.close()
