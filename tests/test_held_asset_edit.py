"""Regression: assets read in one submit can be edited in a later one.

Locks in the "held asset" edit shape: `read(list)` -> submit -> hold the
Assets -> an unrelated submit -> `edit(asset)` per asset (one chain each)
and `edit(list)` (one chain).
"""

from test_wrapper import ID_ONE, ID_TWO, StubDriver, make_asset, make_wrapper

from cascade_cms.cmstypes import Asset, CascadeSuccess, IdentifierType


def _edits(driver: StubDriver) -> list:
    """Every edit request the stub saw, across all batches."""
    return [
        request
        for batch in driver.batches
        for request in batch
        if request.method == "POST" and request.url.endswith("/edit")
    ]


def test_held_assets_can_be_edited_in_later_submits():
    ids = [IdentifierType(id=i, type="page") for i in (ID_ONE, ID_TWO)]
    ok = CascadeSuccess(success=True)
    driver = StubDriver(
        [
            [make_asset(id=ID_ONE, displayName="old")],  # read chain 1
            [make_asset(id=ID_TWO, displayName="old")],  # read chain 2
            [ok],  # unrelated publish
            [ok],  # edit(asset) chain 1
            [ok],  # edit(asset) chain 2
            [ok],  # edit(list) single chain
        ]
    )
    wrapper = make_wrapper(driver)
    try:
        wrapper.operations.read(ids)
        held = wrapper.submit_requests(Asset).success
        assert len(held) == 2

        wrapper.operations.publish(ids[0])
        assert len(wrapper.submit_requests(CascadeSuccess).success) == 1

        for asset in held:
            asset.displayName = "new"
            wrapper.operations.edit(asset)
        per_asset = wrapper.submit_requests(CascadeSuccess)
        assert len(per_asset) == 2
        assert len(per_asset.success) == 2
        assert per_asset.failed == []

        wrapper.operations.edit(held)
        as_list = wrapper.submit_requests(CascadeSuccess)
        assert len(as_list) == 1
        assert len(as_list.success) == 1

        edits = _edits(driver)
        # 2 from the per-asset submit, 2 from the single list-edit chain.
        assert len(edits) == 4
        assert all(e.payload.get("displayName") == "new" for e in edits)
    finally:
        driver.eventLoop.close()
