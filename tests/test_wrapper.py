"""
Test suite for the D5/D8/D9 error-handling redesign: failure classification,
`ChainResults`, `CascadeBatchError`, and `Cascade.__exit__`.
"""

import asyncio
import io
import subprocess
import sys
import textwrap
from unittest.mock import MagicMock

import aiohttp
import pytest
from pydantic import ValidationError

from cascade_cms import Cascade
from cascade_cms.cmstypes import CascadeError, CascadeSuccess, IdentifierType
from cascade_cms.utils.failures import (
    CascadeBatchError,
    ChainFailure,
    ChainResults,
    FailureCategory,
    classify_failure,
)
from cascade_cms.operations import Operations
from cascade_cms.utils.operation_logger import OperationLogger

ID_ONE = "8b320f55ac1001062545a6d2562cee4b"
ID_TWO = "9c431066bd21120736f6b7e3673dff5c"


def make_asset(asset_type: str = "page", **fields):
    from cascade_cms.cmstypes import Asset

    return Asset({"asset": {asset_type: fields}})


# ============================================================================
# D5: failure classification
# ============================================================================


class TestClassifyFailure:
    def test_cascade_error_at_operation_step(self):
        assert classify_failure("operation", CascadeError(message="nope")) == (
            FailureCategory.CASCADE
        )

    def test_aiohttp_error_at_operation_step_is_network(self):
        exc = aiohttp.ClientConnectorError(
            connection_key=MagicMock(), os_error=OSError("boom")
        )
        assert classify_failure("operation", exc) == FailureCategory.NETWORK

    def test_timeout_error_is_network(self):
        assert classify_failure("operation", TimeoutError("slow")) == (
            FailureCategory.NETWORK
        )

    def test_asyncio_timeout_error_is_network(self):
        assert classify_failure("operation", TimeoutError()) == (
            FailureCategory.NETWORK
        )

    def test_connection_error_is_network(self):
        assert classify_failure("operation", ConnectionError("reset")) == (
            FailureCategory.NETWORK
        )

    def test_pydantic_validation_error_is_library(self):
        try:
            CascadeSuccess.model_validate({"success": "not-a-bool", "extra": 1})
        except ValidationError as exc:
            assert classify_failure("operation", exc) == FailureCategory.LIBRARY
        else:
            pytest.fail("expected a ValidationError")

    def test_value_error_at_operation_step_is_library(self):
        assert classify_failure("operation", ValueError("bad")) == FailureCategory.LIBRARY

    def test_any_exception_at_callback_step_is_callback(self):
        assert classify_failure("callback", ValueError("bad")) == FailureCategory.CALLBACK
        assert classify_failure("callback", CascadeError(message="x")) == (
            FailureCategory.CALLBACK
        )

    def test_cancelled_error_at_operation_step_is_library(self):
        assert classify_failure("operation", asyncio.CancelledError()) == (
            FailureCategory.LIBRARY
        )


# ============================================================================
# ChainResults
# ============================================================================


class TestChainResults:
    def test_still_a_list(self):
        results = ChainResults(["a", "b"], [None, None])
        assert list(results) == ["a", "b"]
        assert len(results) == 2
        assert results[0] == "a"

    def test_ok_excludes_failed_entries(self):
        failure = ChainFailure(
            chain_index=2,
            identifier=None,
            step=1,
            step_name="read",
            node_type="operation",
            category=FailureCategory.CASCADE,
            error=CascadeError(message="nope"),
            message="nope",
        )
        results = ChainResults(["good", failure.error], [None, failure])
        assert results.success == ["good"]
        assert results.failed == [failure]

    def test_empty_results(self):
        results = ChainResults()
        assert list(results) == []
        assert results.success == []
        assert results.failed == []


# ============================================================================
# Wrapper integration: build a minimal wrapper against a stub driver
# ============================================================================


class StubDriver:
    """Driver stand-in that answers requests without any network I/O."""

    def __init__(self, responses=None, raise_on_execute=None):
        self.base_url = "https://example.test/api/v1"
        self.responses = list(responses or [])
        self.raise_on_execute = raise_on_execute
        self.batches = []
        self.eventLoop = asyncio.new_event_loop()
        self.closed = False

    def _build_url(self, *segments):
        return "/".join([self.base_url, *map(str, segments)])

    async def execute_requests(self, requests):
        if self.raise_on_execute is not None:
            raise self.raise_on_execute
        self.batches.append(requests)
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def make_wrapper(driver, logger=None, exit_on_failure=True):
    wrapper = object.__new__(Cascade)
    wrapper._driver = driver
    wrapper._logger = logger if logger is not None else MagicMock(spec=OperationLogger)
    wrapper.operations = Operations(driver, _logger=wrapper._logger)
    wrapper._callback_failures = []
    wrapper._has_reportable_failure = False
    wrapper._exit_on_failure = exit_on_failure
    return wrapper


class TestSubmitRequestsChainResults:
    def test_one_result_per_chain_ok_and_failed(self):
        asset1 = make_asset(id=ID_ONE)
        driver = StubDriver([[asset1], [CascadeError(message="denied")]])
        wrapper = make_wrapper(driver)

        wrapper.operations.read(IdentifierType(id=ID_ONE, type="page"))
        wrapper.operations.read(IdentifierType(id=ID_TWO, type="page"))

        try:
            results = wrapper.submit_requests()
        finally:
            driver.eventLoop.close()

        assert isinstance(results, ChainResults)
        assert len(results) == 2
        assert results.success == [asset1]
        assert len(results.failed) == 1
        assert results.failed[0].category == FailureCategory.CASCADE

    def test_callback_exception_recorded_as_callback_category(self):
        driver = StubDriver([[make_asset(id=ID_ONE)]])
        wrapper = make_wrapper(driver)

        def boom(_asset):
            raise ValueError("bad asset")

        wrapper.operations.read(IdentifierType(id=ID_ONE, type="page")).then(boom)

        try:
            results = wrapper.submit_requests()
        finally:
            driver.eventLoop.close()

        assert len(results.failed) == 1
        assert results.failed[0].category == FailureCategory.CALLBACK
        assert wrapper._callback_failures == [results.failed[0]]
        assert wrapper._has_reportable_failure is False

    def test_other_chains_finish_before_callback_exception_is_raised(self):
        """Chain 1's callback raises; chain 2 has no callback and should
        still complete and land in `.success` — a callback exception stops only
        its own chain."""
        driver = StubDriver([[make_asset(id=ID_ONE)], [make_asset(id=ID_TWO)]])
        wrapper = make_wrapper(driver)

        def boom(_asset):
            raise ValueError("bad asset")

        wrapper.operations.read(IdentifierType(id=ID_ONE, type="page")).then(boom)
        wrapper.operations.read(IdentifierType(id=ID_TWO, type="page"))

        try:
            results = wrapper.submit_requests()
        finally:
            driver.eventLoop.close()

        assert len(results.success) == 1
        assert results.success[0].get("id") == ID_TWO
        assert len(results.failed) == 1
        assert results.failed[0].category == FailureCategory.CALLBACK

    def test_batch_error_console_line_carries_prefix(self, tmp_path):
        logger = OperationLogger(server="TESTSRV", debug_config={"log_dir": str(tmp_path)})
        buffer = io.StringIO()
        logger._console_logger.handlers[0].stream = buffer

        driver = StubDriver([])
        wrapper = make_wrapper(driver, logger=logger)
        wrapper.operations.read(IdentifierType(id=ID_ONE, type="page"))

        async def _broken_execute_chains(chains, executor=None):
            raise RuntimeError("event loop broke")

        wrapper._execute_chains = _broken_execute_chains

        try:
            with pytest.raises(CascadeBatchError):
                wrapper.submit_requests()
        finally:
            driver.eventLoop.close()

        assert "[ERROR]: [CASCADE-REST-CMS] RuntimeError — check log" in buffer.getvalue()

    def test_empty_queue_returns_empty_chain_results(self):
        driver = StubDriver([])
        wrapper = make_wrapper(driver)
        try:
            results = wrapper.submit_requests()
        finally:
            driver.eventLoop.close()
        assert isinstance(results, ChainResults)
        assert list(results) == []

    def test_batch_failure_raises_cascade_batch_error_and_clears_queue(self):
        # A driver exception during an operation step is already caught and
        # reported by the chain itself (a LIBRARY failure, not a batch
        # break) — so to exercise the genuine batch-level path (the
        # gather/event-loop machinery breaking before chains can report),
        # patch `_execute_chains` directly, matching the "run itself broke"
        # scope D6 describes.
        driver = StubDriver([])
        wrapper = make_wrapper(driver)
        wrapper.operations.read(IdentifierType(id=ID_ONE, type="page"))

        async def _broken_execute_chains(chains, executor=None):
            raise RuntimeError("event loop broke")

        wrapper._execute_chains = _broken_execute_chains

        try:
            with pytest.raises(CascadeBatchError) as excinfo:
                wrapper.submit_requests()
        finally:
            driver.eventLoop.close()

        assert isinstance(excinfo.value.__cause__, RuntimeError)
        assert wrapper.operations._chains == []
        assert wrapper._has_reportable_failure is True

    def test_tally_strings(self, tmp_path):
        logger = OperationLogger(server="TESTSRV", debug_config={"log_dir": str(tmp_path)})
        driver = StubDriver([[make_asset(id=ID_ONE)]])
        wrapper = make_wrapper(driver, logger=logger)
        wrapper.operations.read(IdentifierType(id=ID_ONE, type="page"))
        try:
            wrapper.submit_requests()
        finally:
            driver.eventLoop.close()
        logger.log_exit()
        assert logger._succeeded_count == 1
        assert logger._failed_count == 0
        assert logger._has_reportable_failure is False

    def test_tally_suffix_only_for_reportable_categories(self, tmp_path):
        logger = OperationLogger(server="TESTSRV", debug_config={"log_dir": str(tmp_path)})
        driver = StubDriver([[CascadeError(message="denied")]])
        wrapper = make_wrapper(driver, logger=logger)
        wrapper.operations.read(IdentifierType(id=ID_ONE, type="page"))
        try:
            wrapper.submit_requests()
        finally:
            driver.eventLoop.close()
        assert logger._has_reportable_failure is True

    def test_tally_no_suffix_for_callback_only_failure(self, tmp_path):
        logger = OperationLogger(server="TESTSRV", debug_config={"log_dir": str(tmp_path)})
        driver = StubDriver([[make_asset(id=ID_ONE)]])
        wrapper = make_wrapper(driver, logger=logger)

        def boom(_asset):
            raise ValueError("bad")

        wrapper.operations.read(IdentifierType(id=ID_ONE, type="page")).then(boom)
        try:
            wrapper.submit_requests()
        finally:
            driver.eventLoop.close()
        assert logger._has_reportable_failure is False

    def test_cumulative_counts_across_two_batches(self, tmp_path):
        logger = OperationLogger(server="TESTSRV", debug_config={"log_dir": str(tmp_path)})
        driver = StubDriver([[make_asset(id=ID_ONE)], [CascadeError(message="denied")]])
        wrapper = make_wrapper(driver, logger=logger)

        wrapper.operations.read(IdentifierType(id=ID_ONE, type="page"))
        wrapper.submit_requests()

        wrapper.operations.read(IdentifierType(id=ID_TWO, type="page"))
        wrapper.submit_requests()

        driver.eventLoop.close()
        assert logger._succeeded_count == 1
        assert logger._failed_count == 1
        assert logger._has_reportable_failure is True


# ============================================================================
# D8: __exit__ behavior
# ============================================================================


class TestExit:
    def test_no_failures_returns_normally(self):
        wrapper = make_wrapper(StubDriver([]))
        assert wrapper.__exit__(None, None, None) is False

    def test_batch_error_raises_system_exit_when_exit_on_failure(self):
        wrapper = make_wrapper(StubDriver([]))
        try:
            raise CascadeBatchError("boom")
        except CascadeBatchError:
            exc_type, exc_value, tb = sys.exc_info()
            with pytest.raises(SystemExit) as excinfo:
                wrapper.__exit__(exc_type, exc_value, tb)
            assert excinfo.value.code == 1

    def test_batch_error_propagates_when_exit_on_failure_false(self):
        wrapper = make_wrapper(StubDriver([]), exit_on_failure=False)
        try:
            raise CascadeBatchError("boom")
        except CascadeBatchError:
            exc_type, exc_value, tb = sys.exc_info()
            assert wrapper.__exit__(exc_type, exc_value, tb) is False

    def test_user_exception_propagates_untouched(self):
        wrapper = make_wrapper(StubDriver([]))
        try:
            raise ValueError("user bug")
        except ValueError:
            exc_type, exc_value, tb = sys.exc_info()
            assert wrapper.__exit__(exc_type, exc_value, tb) is False
        assert wrapper._driver.closed is True

    def test_keyboard_interrupt_propagates_untouched(self):
        wrapper = make_wrapper(StubDriver([]))
        try:
            raise KeyboardInterrupt()
        except KeyboardInterrupt:
            exc_type, exc_value, tb = sys.exc_info()
            assert wrapper.__exit__(exc_type, exc_value, tb) is False

    def test_callback_failure_raised_unwrapped_first_in_creation_order(self):
        first = ValueError("first")
        second = TypeError("second")
        wrapper = make_wrapper(StubDriver([]))
        wrapper._callback_failures = [
            ChainFailure(1, None, 1, "boom", "callback", FailureCategory.CALLBACK, first, "first"),
            ChainFailure(2, None, 1, "boom", "callback", FailureCategory.CALLBACK, second, "second"),
        ]
        with pytest.raises(ValueError, match="first"):
            wrapper.__exit__(None, None, None)

    def test_library_scope_failure_raises_system_exit(self):
        wrapper = make_wrapper(StubDriver([]))
        wrapper._has_reportable_failure = True
        with pytest.raises(SystemExit) as excinfo:
            wrapper.__exit__(None, None, None)
        assert excinfo.value.code == 1

    def test_exit_on_failure_false_never_raises(self):
        wrapper = make_wrapper(StubDriver([]), exit_on_failure=False)
        wrapper._has_reportable_failure = True
        wrapper._callback_failures = [
            ChainFailure(1, None, 1, "boom", "callback", FailureCategory.CALLBACK, ValueError(), "x")
        ]
        assert wrapper.__exit__(None, None, None) is False

    def test_cleanup_runs_before_any_raise(self):
        wrapper = make_wrapper(StubDriver([]))
        wrapper._has_reportable_failure = True
        with pytest.raises(SystemExit):
            wrapper.__exit__(None, None, None)
        assert wrapper._driver.closed is True


# ============================================================================
# Acceptance scenario #10-adjacent: real subprocess exit codes
# ============================================================================


def _run_script(body: str) -> subprocess.CompletedProcess:
    import os

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_dir = os.path.join(repo_root, "src")
    preamble = (
        "import sys\n"
        f"sys.path.insert(0, {src_dir!r})\n"
        f"sys.path.insert(0, {repo_root!r})\n"
    )
    script = preamble + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )


class TestRealProcessExitCode:
    def test_exit_code_one_on_failure(self):
        result = _run_script(
            textwrap.dedent(
                """
                from unittest.mock import MagicMock
                from tests.test_wrapper import StubDriver, make_wrapper
                from cascade_cms.cmstypes import CascadeError
                from cascade_cms.operations import Operations
                from cascade_cms import Cascade

                driver = StubDriver([[CascadeError(message="denied")]])
                wrapper = make_wrapper(driver)
                from cascade_cms.cmstypes import IdentifierType
                wrapper.operations.read(IdentifierType(id="8b320f55ac1001062545a6d2562cee4b", type="page"))
                with wrapper:
                    wrapper.submit_requests()
                print("UNREACHABLE")
                """
            )
        )
        assert result.returncode == 1
        assert "UNREACHABLE" not in result.stdout

    def test_exit_code_zero_on_success(self):
        result = _run_script(
            textwrap.dedent(
                """
                from tests.test_wrapper import StubDriver, make_wrapper, make_asset
                from cascade_cms.cmstypes import IdentifierType

                driver = StubDriver([[make_asset(id="8b320f55ac1001062545a6d2562cee4b")]])
                wrapper = make_wrapper(driver)
                wrapper.operations.read(IdentifierType(id="8b320f55ac1001062545a6d2562cee4b", type="page"))
                with wrapper:
                    wrapper.submit_requests()
                print("REACHED")
                """
            )
        )
        assert result.returncode == 0
        assert "REACHED" in result.stdout
