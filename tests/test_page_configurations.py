"""Page-configuration accessors write region `content` through to the payload."""

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
    asset_adapter,
)


def _page(**extra) -> Asset:
    return make_asset(
        id=ID_ONE,
        pageConfigurations=[
            {
                "name": "ASPX",
                "templateId": "t1",
                "pageRegions": [
                    {
                        "name": "DEFAULT",
                        "blockId": "b1",
                        "noBlock": False,
                        "content": "old",
                    },
                    {"name": "FOOTER", "blockId": None, "content": "f"},
                ],
            }
        ],
        **extra,
    )


def _payload(asset: Asset) -> dict:
    return json.loads(asset_adapter.dump_json(asset))["asset"]["page"]


def _region(asset: Asset, name: str = "DEFAULT") -> PageRegion:
    region = asset.get_page_configuration("ASPX", name)
    assert isinstance(region, PageRegion)
    return region


def test_accessor_edit_reaches_payload():
    asset = _page()
    _region(asset).content = "NEW"
    regions = _payload(asset)["pageConfigurations"][0]["pageRegions"]
    assert regions[0]["content"] == "NEW"
    assert regions[1]["content"] == "f"


def test_edit_via_config_page_regions_reaches_payload():
    asset = _page()
    config = asset.get_page_configuration("ASPX")
    assert isinstance(config, PageConfiguration)
    config.page_regions[1].content = "NEW-FOOTER"
    regions = _payload(asset)["pageConfigurations"][0]["pageRegions"]
    assert regions[1]["content"] == "NEW-FOOTER"
    assert regions[0]["content"] == "old"


def test_unmodelled_fields_preserved():
    asset = _page()
    _region(asset).content = "NEW"
    config = _payload(asset)["pageConfigurations"][0]
    assert config["templateId"] == "t1"
    assert config["pageRegions"][0]["blockId"] == "b1"
    assert config["pageRegions"][0]["noBlock"] is False


def test_raw_edit_visible_through_accessor():
    asset = _page()
    raw = asset._data["pageConfigurations"][0]["pageRegions"][0]
    raw["content"] = "RAW"
    assert _region(asset).content == "RAW"


def test_wholesale_reassignment_visible():
    asset = _page()
    asset.pageConfigurations = [
        {"name": "XML", "pageRegions": [{"name": "DEFAULT", "content": "x"}]}
    ]
    assert asset.get_page_configuration("ASPX") is None
    region = asset.get_page_configuration("XML", "DEFAULT")
    assert isinstance(region, PageRegion)
    assert region.content == "x"
    region.content = "y"
    assert _payload(asset)["pageConfigurations"][0]["pageRegions"][0]["content"] == "y"


def test_invalid_content_raises_and_payload_unchanged():
    asset = _page()
    region = _region(asset)
    with pytest.raises(ValidationError):
        region.content = 123  # type: ignore[assignment]
    assert _payload(asset)["pageConfigurations"][0]["pageRegions"][0]["content"] == "old"


def test_content_none_is_written():
    asset = _page()
    _region(asset).content = None
    assert _payload(asset)["pageConfigurations"][0]["pageRegions"][0]["content"] is None


def test_assigning_name_raises():
    asset = _page()
    region = _region(asset)
    with pytest.raises(AttributeError, match="PageRegion.name is read-only"):
        region.name = "OTHER"
    config = asset.get_page_configuration("ASPX")
    assert isinstance(config, PageConfiguration)
    with pytest.raises(AttributeError, match="PageConfiguration.name is read-only"):
        config.name = "OTHER"
    assert _payload(asset)["pageConfigurations"][0]["name"] == "ASPX"


def test_construction_from_raw_data_unaffected():
    region = PageRegion(name="DEFAULT", content="c")
    config = PageConfiguration(name="ASPX", pageRegions=[{"name": "DEFAULT"}])
    assert region.name == "DEFAULT"
    assert config.page_regions[0].name == "DEFAULT"
    region.content = "d"  # no raw dict attached: plain validated assignment
    assert region.content == "d"


def test_misses_return_none():
    asset = _page()
    assert asset.get_page_configuration("NOPE") is None
    assert asset.get_page_configuration("ASPX", "NOPE") is None
    assert make_asset(id=ID_ONE).get_page_configuration("ASPX") is None


def test_two_configurations_edit_reaches_only_the_intended_one():
    asset = make_asset(
        id=ID_ONE,
        pageConfigurations=[
            {"name": "ASPX", "pageRegions": [{"name": "DEFAULT", "content": "a"}]},
            {"name": "XML", "pageRegions": [{"name": "DEFAULT", "content": "x"}]},
        ],
    )
    region = asset.get_page_configuration("XML", "DEFAULT")
    assert isinstance(region, PageRegion)
    region.content = "X2"
    configs = _payload(asset)["pageConfigurations"]
    assert configs[0]["pageRegions"][0]["content"] == "a"
    assert configs[1]["pageRegions"][0]["content"] == "X2"


def test_page_configs_compat_list_of_page_configuration():
    asset = _page()
    assert isinstance(asset._page_configs, list)
    assert all(isinstance(c, PageConfiguration) for c in asset._page_configs)
    assert [c.name for c in asset._page_configs] == ["ASPX"]


def test_malformed_config_fails_at_creation():
    with pytest.raises(ValidationError):
        make_asset(id=ID_ONE, pageConfigurations=[{"name": "ASPX"}])


def test_edit_chain_sends_new_content():
    ident = IdentifierType(id=ID_ONE, type="page")
    driver = StubDriver([[_page()], [CascadeSuccess(success=True)]])
    wrapper = make_wrapper(driver)
    try:

        def rewrite(asset: Asset) -> Asset:
            region = asset.get_page_configuration("ASPX", "DEFAULT")
            assert isinstance(region, PageRegion)
            region.content = "NEW"
            return asset

        wrapper.operations.read(ident).edit(rewrite)
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
        assert sent["pageConfigurations"][0]["pageRegions"][0]["content"] == "NEW"
    finally:
        driver.eventLoop.close()
