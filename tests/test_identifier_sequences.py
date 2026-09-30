"""Multi-identifier Operations methods accept any Sequence (list or tuple)."""

import asyncio
from unittest.mock import MagicMock

import pytest

from cascade_cms.cmstypes import IdentifierType, Path
from cascade_cms.driver import CascadeCMSRestDriver
from cascade_cms.operations import ChainGroup, OperationChain, Operations
from cascade_cms.utils.operation_logger import OperationLogger

ID_ONE = "8b320f55ac1001062545a6d2562cee4b"
ID_TWO = "9c431066bd21120736f6b7e3673dff5c"
MESSAGE = "expected an IdentifierType, a Path or a list/tuple of them"

# method name -> extra positional args after the identifier
METHODS = {
    "read": (),
    "delete": (),
    "copy": (MagicMock(),),
    "move": (MagicMock(),),
    "publish": (),
    "checkIn": (MagicMock(),),
    "checkOut": (),
    "listSubscribers": (),
    "readAccessRights": (),
}


@pytest.fixture
def operations():
    driver = MagicMock(spec=CascadeCMSRestDriver)
    driver.eventLoop = asyncio.new_event_loop()
    yield Operations(driver, _logger=MagicMock(spec=OperationLogger))
    driver.eventLoop.close()


@pytest.fixture
def by_id():
    return IdentifierType(identifier=ID_ONE, asset_type="page")


@pytest.fixture
def by_path():
    return Path(asset_type="page", site_name="site", path="/a")


@pytest.mark.parametrize("method", METHODS)
class TestSequences:
    def call(self, operations, method, identifier):
        return getattr(operations, method)(identifier, *METHODS[method])

    def test_list(self, operations, method, by_id):
        group = self.call(operations, method, [by_id, by_id])
        assert isinstance(group, ChainGroup)
        assert len(group) == 2

    def test_tuple(self, operations, method, by_id):
        group = self.call(operations, method, (by_id, by_id))
        assert isinstance(group, ChainGroup)
        assert len(group) == 2

    def test_mixed_tuple(self, operations, method, by_id, by_path):
        group = self.call(operations, method, (by_id, by_path))
        assert isinstance(group, ChainGroup)
        assert len(group) == 2

    def test_single_is_unchanged(self, operations, method, by_id):
        chain = self.call(operations, method, by_id)
        assert isinstance(chain, OperationChain)
        assert len(operations._chains) == 1

    def test_single_path_is_unchanged(self, operations, method, by_path):
        chain = self.call(operations, method, by_path)
        assert isinstance(chain, OperationChain)

    def test_empty_list_is_an_empty_group(self, operations, method):
        group = self.call(operations, method, [])
        assert isinstance(group, ChainGroup)
        assert len(group) == 0

    @pytest.mark.parametrize("bad", ["abc", b"x", 3, None])
    def test_non_identifiers_raise_type_error(self, operations, method, bad):
        with pytest.raises(TypeError, match=MESSAGE):
            self.call(operations, method, bad)
        assert operations._chains == []


def test_type_error_names_the_offending_type(operations):
    with pytest.raises(TypeError, match="not str"):
        operations.read("abc")


def test_delete_callable_may_return_a_tuple(operations, by_id):
    chain = operations.read(by_id).delete(lambda previous: (by_id, by_id))
    requests = chain._build_delete_requests(
        None, parser=None, payload=None, resolver=lambda previous: (by_id, by_id)
    )
    assert len(requests) == 2
