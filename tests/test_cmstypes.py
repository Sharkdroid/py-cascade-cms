from uuid import UUID

import pytest

from cascade_cms.cmstypes import Path, resolve_identifier


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
