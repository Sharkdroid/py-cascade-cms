import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# edit_test.py matches pytest's default `*_test.py` collection glob, so
# without this, `pytest tests/` would import (and execute, at module scope)
# these manual human-run scripts against a live Cascade instance instead of
# treating them as scripts a person runs directly with `python tests/x.py`.
collect_ignore = ["edit_test.py", "testing.py"]


@pytest.fixture
def page_identifier():
    from uuid import UUID

    from cascade_cms.cmstypes import IdentifierType

    return IdentifierType(
        identifier=UUID("8b320f55ac1001062545a6d2562cee4b"),
        asset_type="page",
    )
