import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture
def page_identifier():
    from uuid import UUID

    from cascade_cms.cmstypes import IdentifierType

    return IdentifierType(
        identifier=UUID("8b320f55ac1001062545a6d2562cee4b"),
        asset_type="page",
    )
