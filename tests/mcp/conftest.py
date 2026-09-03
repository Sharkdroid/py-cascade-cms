# tests/mcp/*.py auto-collects fine under the root tests/conftest.py's
# sys.path setup. smoke_test.py matches pytest's default `*_test.py`
# collection glob (like tests/edit_test.py does) but is a manual human-run
# script against a real dev site, not a pytest test - exclude it the same
# way the root conftest.py excludes edit_test.py/testing.py.
collect_ignore = ["smoke_test.py"]

from unittest.mock import MagicMock

import pytest


class SequentialFakeWrapper:
    """Stands in for CascadeWrapperBase across a MULTI-hop resolution chain:
    each submit_requests() call returns the next pre-baked result in order,
    mirroring how resolution.py reuses one wrapper/cascade for several
    sequential reads within a single tool call."""

    def __init__(self, results: list):
        self.operations = MagicMock()
        self._results = list(results)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def submit_requests(self, *args, **kwargs):
        return [self._results.pop(0)]


@pytest.fixture
def patch_wrapper_sequence(monkeypatch):
    """Patches cascade_cms.mcp.server._wrapper to return a SequentialFakeWrapper
    seeded with `results`, one per submit_requests() call, in order."""

    def _patch(results: list):
        from cascade_cms.mcp import server

        monkeypatch.setattr(server, "_wrapper", lambda: SequentialFakeWrapper(results))

    return _patch
