"""Static typing checks: run `mypy --warn-unused-ignores` on this file.

Every `# type: ignore[...]` below must be USED, so each one proves that
mypy really rejects that call. Imports only cascade_cms.
"""

from typing import assert_type

from cascade_cms.cmstypes import (
    Asset,
    IdentifierType,
    NewAsset,
    PageConfiguration,
    PageRegion,
    Path,
)
from cascade_cms.operations import ChainGroup, OperationChain, Operations

UID = "8b320f55ac1001062545a6d2562cee4b"


def multi_identifiers(ops: Operations) -> None:
    by_id = IdentifierType(identifier=UID, asset_type="page")
    by_path = Path(asset_type="page", site_name="s", path="/a")
    ids: list[IdentifierType] = [by_id]
    paths: list[Path] = [by_path]

    assert_type(ops.read(ids), OperationChain | ChainGroup)
    ops.read(paths)
    ops.read(by_id)
    ops.read([by_id, by_path])
    ops.read((by_id, by_path))
    ops.publish(ids)
    ops.delete((by_id, by_id))

    ops.read("nope")  # type: ignore[arg-type]
    ops.read(3)  # type: ignore[arg-type]


def page_configuration(asset: Asset, region: str | None) -> None:
    assert_type(asset.get_page_configuration("x"), PageConfiguration | None)
    named = asset.get_page_configuration("x", "R")
    assert_type(named, PageRegion | None)
    assert_type(
        asset.get_page_configuration("x", page_region="R"), PageRegion | None
    )
    asset.get_page_configuration("x", region)  # str | None is accepted
    if named is not None:
        _ = (named.block_id, named.block_path)  # no [union-attr]


def constructors() -> None:
    IdentifierType(id=UID, type="page")
    IdentifierType(identifier=UID, asset_type="page")
    IdentifierType(id=UID, type="page", path={"path": "/a", "siteName": "s"})
    NewAsset(
        name="n",
        asset_type="page",
        site_name="s",
        parent_folder_path="/",
        metadata={},
    )

    IdentifierType(id=UID, asset_type="page")  # type: ignore[call-overload]
    IdentifierType(id=UID, type="nonsense")  # type: ignore[call-overload]
    IdentifierType(id=UID)  # type: ignore[call-overload]
    NewAsset(name="n", site_name="s", parent_folder_path="/")  # type: ignore[call-arg]

