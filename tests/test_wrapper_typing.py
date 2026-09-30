"""The context manager is typed: `with wrapper as cascade` is a
`CascadeWrapperBase`, and typed results keep their type.

The `assert_type` calls are no-ops at runtime; they fail under
`mypy tests/test_wrapper_typing.py` if `__enter__` is not annotated.
"""

import asyncio
from typing import assert_type
from unittest.mock import MagicMock

from cascade_cms import CascadeWrapperBase
from cascade_cms.cmstypes import Asset
from cascade_cms.operations import Operations
from cascade_cms.utils.operation_logger import OperationLogger


class _StubDriver:
    def __init__(self) -> None:
        self.base_url = "https://example.test/api/v1"
        self.eventLoop = asyncio.new_event_loop()

    def close(self) -> None:
        self.eventLoop.close()


def _make_wrapper() -> CascadeWrapperBase:
    driver = _StubDriver()
    wrapper = object.__new__(CascadeWrapperBase)
    wrapper._driver = driver  # type: ignore[assignment]
    wrapper._logger = MagicMock(spec=OperationLogger)
    wrapper.operations = Operations(driver, _logger=wrapper._logger)  # type: ignore[arg-type]
    wrapper._callback_failures = []
    wrapper._has_reportable_failure = False
    wrapper._exit_on_failure = True
    return wrapper


def test_enter_returns_same_object() -> None:
    wrapper = _make_wrapper()
    with wrapper as cascade:
        # Before the identity check: `is` would narrow an `Any`.
        assert_type(cascade, CascadeWrapperBase)
        assert cascade is wrapper


def _static_result_type(cascade: CascadeWrapperBase) -> None:
    for result in cascade.submit_requests(Asset).success:
        assert_type(result, Asset)
