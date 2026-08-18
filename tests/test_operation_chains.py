"""
Test suite for linked-list operation chain architecture.

Tests the new OperationChain and Node classes that replace the pooled-callbacks model.
Each chain executes independently with its own request/callback sequence.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from cascade_cms import CascadeWrapperBase
from cascade_cms.cmstypes import (
    Asset,
    CascadeError,
    CascadeSuccess,
    IdentifierType,
    Path,
)
from cascade_cms.driver import CascadeCMSRestDriver
from cascade_cms.operation_logger import OperationLogger
from cascade_cms.operations import Node, OperationChain, Operations

ID_ONE = "8b320f55ac1001062545a6d2562cee4b"
ID_TWO = "9c431066bd21120736f6b7e3673dff5c"


def make_asset(asset_type: str = "page", **fields) -> Asset:
    """Build an Asset from the response envelope Cascade actually returns."""
    return Asset({"asset": {asset_type: fields}})


def walk(chain: OperationChain) -> list[Node]:
    """Collect every node in a chain, head to tail."""
    nodes = []
    node = chain._head
    while node is not None:
        nodes.append(node)
        node = node.next
    return nodes


@pytest.fixture
def mock_driver():
    """Mock driver that doesn't make real HTTP calls."""
    driver = MagicMock(spec=CascadeCMSRestDriver)
    driver.eventLoop = asyncio.new_event_loop()
    driver._submitRequests = MagicMock()
    yield driver
    driver.eventLoop.close()


@pytest.fixture
def mock_logger():
    """Mock logger."""
    return MagicMock(spec=OperationLogger)


@pytest.fixture
def operations(mock_driver, mock_logger):
    """Create Operations instance with mocked dependencies."""
    return Operations(mock_driver, _logger=mock_logger)


# ============================================================================
# T1: Single Chain Execution
# ============================================================================

class TestSingleChainExecution:
    """Test basic read → edit chain."""

    def test_chain_created_on_read_call(self, operations, mock_driver):
        """Reading creates a new OperationChain."""
        identifier = IdentifierType(id=ID_ONE, type="page")

        chain = operations.read(identifier)

        assert isinstance(chain, OperationChain)
        assert isinstance(chain._head, Node)
        assert chain._head.node_type == "operation"
        assert chain._head.operation_type == "read"

    def test_chain_contains_read_and_edit_nodes(self, operations):
        """Chaining read → edit creates 2 operation nodes."""
        identifier = IdentifierType(id=ID_ONE, type="page")
        payload = {"page": {"name": "Updated"}}

        chain = operations.read(identifier).edit(identifier, payload)

        # Walk the chain
        nodes = walk(chain)
        node_types = [node.operation_type for node in nodes]

        assert len(nodes) == 2
        assert node_types == ["read", "edit"]
        assert chain._current is nodes[-1]  # Should point to last node added

    def test_chain_without_callbacks_returns_final_result(self, operations, mock_driver):
        """Simple read → edit returns edit result."""
        identifier = IdentifierType(id=ID_ONE, type="page")
        payload = {"page": {"name": "Updated"}}

        expected_result = make_asset(id=ID_ONE, name="Updated")
        mock_driver._submitRequests.return_value = [expected_result]

        chain = operations.read(identifier).edit(identifier, payload)
        result = chain.execute(mock_driver)

        # Should get the result from the operation
        assert result == expected_result


# ============================================================================
# T2: Chain with Multiple Callbacks
# ============================================================================

class TestChainWithMultipleCallbacks:
    """Test read → callback1 → callback2 → edit."""

    def test_chain_with_two_callbacks(self, operations):
        """Multiple .then() calls create callback nodes."""
        identifier = IdentifierType(id=ID_ONE, type="page")
        payload = {"page": {"name": "Updated"}}

        def validate(result):
            return result

        def transform(result):
            return result

        chain = (operations.read(identifier)
                 .then(validate)
                 .then(transform)
                 .edit(identifier, payload))

        # Count nodes
        nodes = walk(chain)

        assert len(nodes) == 4  # read, callback, callback, edit
        assert nodes[0].node_type == "operation"
        assert nodes[0].operation_type == "read"
        assert nodes[1].node_type == "callback"
        assert nodes[2].node_type == "callback"
        assert nodes[3].node_type == "operation"
        assert nodes[3].operation_type == "edit"

    def test_callbacks_execute_in_order(self, operations, mock_driver):
        """Callbacks process result sequentially."""
        identifier = IdentifierType(id=ID_ONE, type="page")
        payload = {"page": {"name": "Updated"}}

        call_order = []

        def cb1(result):
            call_order.append("cb1")
            return {"processed": True, "asset": result}

        def cb2(result):
            call_order.append("cb2")
            return {**result, "validated": True}

        initial_result = make_asset(id=ID_ONE)
        edit_result = CascadeSuccess()

        # Mock: first call (read) returns initial, second call (edit) returns success
        mock_driver._submitRequests.side_effect = [[initial_result], [edit_result]]

        chain = (operations.read(identifier)
                 .then(cb1)
                 .then(cb2)
                 .edit(identifier, payload))

        result = chain.execute(mock_driver)

        assert call_order == ["cb1", "cb2"]
        assert result == edit_result

    def test_callback_result_passed_to_next_node(self, operations, mock_driver):
        """Each callback's output becomes input to next."""
        identifier = IdentifierType(id=ID_ONE, type="page")
        payload = {"page": {"name": "Updated"}}

        received_values = []

        def cb1(result):
            received_values.append(("cb1", result))
            return {"step": 1, "asset": result}

        def cb2(result):
            received_values.append(("cb2", result))
            return {**result, "step": 2}

        initial_asset = make_asset(id=ID_ONE, name="Original")
        edit_result = CascadeSuccess()

        mock_driver._submitRequests.side_effect = [[initial_asset], [edit_result]]

        chain = (operations.read(identifier)
                 .then(cb1)
                 .then(cb2)
                 .edit(identifier, payload))

        chain.execute(mock_driver)

        # cb1 receives original asset
        assert received_values[0][0] == "cb1"
        assert received_values[0][1] is initial_asset
        # cb2 receives cb1's output (should have "step": 1)
        assert received_values[1][0] == "cb2"
        assert received_values[1][1].get("step") == 1

    def test_callback_returning_none_passes_previous_result_through(
        self, operations, mock_driver
    ):
        """A side-effect callback doesn't blank out the chain's result."""
        identifier = IdentifierType(id=ID_ONE, type="page")

        seen = []

        def side_effect_only(result):
            seen.append(result)

        asset = make_asset(id=ID_ONE)
        mock_driver._submitRequests.return_value = [asset]

        chain = operations.read(identifier).then(side_effect_only)
        result = chain.execute(mock_driver)

        assert seen == [asset]
        assert result is asset


# ============================================================================
# T3: Multiple Independent Chains
# ============================================================================

class TestMultipleIndependentChains:
    """Test that cascade.operations calls create separate chains."""

    def test_multiple_read_calls_create_separate_chains(self, operations):
        """Each cascade.operations.read() starts a new chain."""
        id1 = IdentifierType(id=ID_ONE, type="page")
        id2 = IdentifierType(id=ID_TWO, type="page")
        payload1 = {"page": {"name": "A"}}
        payload2 = {"page": {"name": "B"}}

        def passthrough(result):
            return result

        # Two separate calls to operations.read() = two chains
        chain1 = operations.read(id1).then(passthrough).edit(id1, payload1)
        chain2 = operations.read(id2).edit(id2, payload2)

        assert len(operations._chains) == 2
        assert operations._chains[0] is chain1
        assert operations._chains[1] is chain2

    def test_chains_execute_independently(self, mock_driver, mock_logger):
        """Two chains execute without interference."""
        ops = Operations(mock_driver, _logger=mock_logger)

        id1 = IdentifierType(id=ID_ONE, type="page")
        id2 = IdentifierType(id=ID_TWO, type="page")
        payload = {"page": {"name": "Updated"}}

        read1 = make_asset(id=ID_ONE)
        read2 = make_asset(id=ID_TWO)
        result1 = make_asset(id=ID_ONE, name="Updated")
        result2 = CascadeSuccess()

        # One entry per operation node: read, edit, read, delete
        mock_driver._submitRequests.side_effect = [
            [read1], [result1], [read2], [result2],
        ]

        ops.read(id1).edit(id1, payload)
        ops.read(id2).delete(id2)

        # Execute both chains
        results = []
        for chain in ops._chains:
            results.append(chain.execute(mock_driver))

        assert len(results) == 2
        assert results[0] == result1
        assert results[1] == result2


# ============================================================================
# T4: Chain Error Stops Only That Chain
# ============================================================================

class TestChainErrorIsolation:
    """Test that errors in one chain don't block others."""

    def test_callback_exception_stops_chain(self, operations, mock_driver):
        """Callback exception stops that chain, doesn't block others."""
        id1 = IdentifierType(id=ID_ONE, type="page")
        id2 = IdentifierType(id=ID_TWO, type="page")
        payload = {"page": {"name": "Updated"}}

        def failing_callback(result):
            raise ValueError("Invalid data")

        asset1 = make_asset(id=ID_ONE)
        asset2 = make_asset(id=ID_TWO)
        edit_result = CascadeSuccess()

        # Chain 1 stops after its read, so only three requests ever run.
        mock_driver._submitRequests.side_effect = [[asset1], [asset2], [edit_result]]

        operations.read(id1).then(failing_callback).edit(id1, payload)
        operations.read(id2).edit(id2, payload)

        results = []
        for chain in operations._chains:
            try:
                result = chain.execute(mock_driver)
                results.append(result)
            except ValueError as e:
                results.append(e)

        # Chain 1: callback exception
        assert isinstance(results[0], ValueError)
        assert str(results[0]) == "Invalid data"
        # Chain 2: should execute successfully
        assert results[1] is not None
        assert results[1] == edit_result
        # Chain 1's edit never ran: read, read, edit — three calls, not four
        assert mock_driver._submitRequests.call_count == 3

    def test_callback_exception_in_results(self, operations, mock_driver):
        """Exception from callback is added to results (Option B)."""
        id1 = IdentifierType(id=ID_ONE, type="page")
        payload = {"page": {"name": "Updated"}}

        def failing_callback(result):
            raise RuntimeError("Processing failed")

        asset = make_asset(id=ID_ONE)
        mock_driver._submitRequests.return_value = [asset]

        chain = operations.read(id1).then(failing_callback).edit(id1, payload)

        # Execution should catch the exception
        result = chain.execute(mock_driver)

        # Result should be the exception object itself (Option B)
        assert isinstance(result, RuntimeError)
        assert str(result) == "Processing failed"


# ============================================================================
# T5: Operation Failure Stops Chain
# ============================================================================

class TestOperationFailure:
    """Test that CascadeError from operations stops chain."""

    def test_operation_cascade_error_stops_chain(self, operations, mock_driver):
        """CascadeError from read stops entire chain."""
        identifier = IdentifierType(id=ID_ONE, type="page")
        payload = {"page": {"name": "Updated"}}

        callbacks_run = []

        def should_not_run(result):
            callbacks_run.append(result)
            return result

        cascade_error = CascadeError(message="Asset not found")
        mock_driver._submitRequests.return_value = [cascade_error]

        chain = operations.read(identifier).then(should_not_run).edit(identifier, payload)
        result = chain.execute(mock_driver)

        # Should get the CascadeError
        assert isinstance(result, CascadeError)
        # Callback and edit never ran
        assert callbacks_run == []
        assert mock_driver._submitRequests.call_count == 1

    def test_operation_error_logged_with_context(self, operations, mock_driver, mock_logger):
        """Operation errors logged with step number and op type."""
        identifier = IdentifierType(id=ID_ONE, type="page")

        cascade_error = CascadeError(message="Not found")
        mock_driver._submitRequests.return_value = [cascade_error]

        chain = operations.read(identifier)
        result = chain.execute(mock_driver)

        # Logger records which chain, which step, and which node failed
        mock_logger.log_chain_error.assert_called_once()
        args = mock_logger.log_chain_error.call_args.args
        assert args[1] == identifier      # asset identifier
        assert args[2] == 1               # step number
        assert args[3] == "operation"     # node type
        assert args[4] == "read"          # operation type
        assert isinstance(result, CascadeError)


# ============================================================================
# T6: Callback Chain (Multiple .then() in a Row)
# ============================================================================

class TestMultipleCallbacksInRow:
    """Test multiple .then() calls create proper callback nodes."""

    def test_three_callbacks_in_sequence(self, operations):
        """Three .then() calls create three callback nodes."""
        identifier = IdentifierType(id=ID_ONE, type="page")

        def fn1(x):
            return x

        def fn2(x):
            return x

        def fn3(x):
            return x

        chain = (operations.read(identifier)
                 .then(fn1)
                 .then(fn2)
                 .then(fn3))

        # Count and verify nodes
        nodes = walk(chain)

        assert len(nodes) == 4  # read + 3 callbacks
        assert nodes[0].operation_type == "read"
        assert nodes[1].node_type == "callback"
        assert nodes[2].node_type == "callback"
        assert nodes[3].node_type == "callback"
        assert [n.callback_fn for n in nodes[1:]] == [fn1, fn2, fn3]

    def test_then_with_list_of_callbacks(self, operations):
        """Passing list to .then() expands into individual callback nodes."""
        identifier = IdentifierType(id=ID_ONE, type="page")

        def fn1(x):
            return x

        def fn2(x):
            return x

        def fn3(x):
            return x

        chain = (operations.read(identifier)
                 .then([fn1, fn2])
                 .then(fn3))

        # Count nodes
        nodes = walk(chain)

        # Should be: read, cb1, cb2, cb3
        assert len(nodes) == 4
        callback_nodes = [n for n in nodes if n.node_type == "callback"]
        assert len(callback_nodes) == 3


# ============================================================================
# T7: Mixed Operations with Callbacks
# ============================================================================

class TestMixedOperationsAndCallbacks:
    """Test read → callback → edit → callback → publish."""

    def test_complex_chain_structure(self, operations):
        """Complex chain: read → cb → edit → cb → publish."""
        id1 = IdentifierType(id=ID_ONE, type="page")
        payload = {"page": {"name": "Updated"}}

        def passthrough(x):
            return x

        chain = (operations.read(id1)
                 .then(passthrough)
                 .edit(id1, payload)
                 .then(passthrough)
                 .publish(id1))

        # Verify structure
        nodes = walk(chain)

        expected_types = ["read", "callback", "edit", "callback", "publish"]
        expected_types_actual = [
            n.operation_type if n.node_type == "operation" else "callback"
            for n in nodes
        ]

        assert expected_types_actual == expected_types

    def test_callable_payload_receives_previous_result(self, operations, mock_driver):
        """edit(identifier, callable) builds its payload from the previous node."""
        id1 = IdentifierType(id=ID_ONE, type="page")

        asset = make_asset(id=ID_ONE, name="Original", path="/about")
        edit_result = CascadeSuccess()
        mock_driver._submitRequests.side_effect = [[asset], [edit_result]]

        seen = []

        def build_payload(previous):
            seen.append(previous)
            return {"page": {"name": "Rewritten"}, "path": "/about"}

        chain = operations.read(id1).edit(id1, build_payload)
        result = chain.execute(mock_driver)

        assert seen == [asset]
        assert result == edit_result
        edit_requests = mock_driver._submitRequests.call_args_list[1].args[0]
        assert edit_requests[0].payload == {
            "page": {"name": "Rewritten"}, "path": "/about"
        }


# ============================================================================
# T8: Chain Reset After Submit
# ============================================================================

class TestChainReset:
    """Test that chains are cleared after submit_requests()."""

    def test_chains_cleared_after_submit(self, operations, mock_driver):
        """Chains list is empty after submit_requests()."""
        id1 = IdentifierType(id=ID_ONE, type="page")

        asset = make_asset(id=ID_ONE)
        mock_driver._submitRequests.return_value = [asset]

        # First batch
        operations.read(id1)
        assert len(operations._chains) == 1

        # Reset (simulating submit_requests call)
        operations._reset_chains()
        assert len(operations._chains) == 0

        # Second batch
        chain2 = operations.read(id1)
        assert len(operations._chains) == 1
        assert operations._chains[0] is chain2

    def test_no_leakage_between_batches(self, operations, mock_driver):
        """Callbacks from batch 1 don't run in batch 2."""
        id1 = IdentifierType(id=ID_ONE, type="page")
        id2 = IdentifierType(id=ID_TWO, type="page")

        calls = []

        def cb1(x):
            calls.append("batch1")
            return x

        def cb2(x):
            calls.append("batch2")
            return x

        asset1 = make_asset(id=ID_ONE)
        asset2 = make_asset(id=ID_TWO)

        # Batch 1
        mock_driver._submitRequests.return_value = [asset1]
        chain1 = operations.read(id1).then(cb1)
        chain1.execute(mock_driver)

        operations._reset_chains()

        # Batch 2
        calls.clear()
        mock_driver._submitRequests.return_value = [asset2]
        chain2 = operations.read(id2).then(cb2)
        chain2.execute(mock_driver)

        # Only cb2 should have run
        assert calls == ["batch2"]


# ============================================================================
# T9: Logger Verbosity
# ============================================================================

class TestLoggerVerbosity:
    """Test logger output changes with debug flag."""

    def test_logger_called_during_chain_execution(self, operations, mock_driver, mock_logger):
        """Logger methods are called during chain execution."""
        identifier = IdentifierType(id=ID_ONE, type="page")

        def validate(result):
            return result

        asset = make_asset(id=ID_ONE)
        mock_driver._submitRequests.return_value = [asset]

        chain = operations.read(identifier).then(validate)
        chain.execute(mock_driver)

        # The chain reports its own progress: start, each node, completion
        mock_logger.log_chain_start.assert_called_once_with(1, identifier)
        assert mock_logger.log_node_execution.call_count == 2
        assert mock_logger.log_node_execution.call_args_list[0].args[:3] == (
            1, "operation", "read",
        )
        assert mock_logger.log_node_execution.call_args_list[1].args[:2] == (
            2, "callback",
        )
        mock_logger.log_chain_complete.assert_called_once()
        mock_logger.log_chain_error.assert_not_called()

    def test_debug_config_controls_node_logging(self, tmp_path):
        """Node logging is debug-only; normal mode stays quiet about steps."""
        normal = OperationLogger(server="test", debug_config=None)
        debug = OperationLogger(
            server="test",
            debug_config={"log_dir": str(tmp_path), "log_operations": True},
        )

        normal.log_node_execution(1, "operation", "read", None)
        debug.log_node_execution(1, "operation", "read", None)

        normal_lines = _logged_lines(normal)
        debug_lines = _logged_lines(debug)

        assert normal_lines == []
        assert any("Step 1: operation (read)" in line for line in debug_lines)


def _logged_lines(logger: OperationLogger) -> list[str]:
    """Read back whatever a logger wrote to its logfile."""
    handler = logger._file_logger.handlers[0]
    handler.flush()
    with open(handler.baseFilename) as fh:
        return fh.read().splitlines()


# ============================================================================
# T10: Executor Passthrough
# ============================================================================

class TestExecutorPassthrough:
    """Test that executor is passed through to callbacks."""

    def test_executor_used_for_sync_callbacks(self, operations, mock_driver):
        """ThreadPoolExecutor passed to callbacks."""
        identifier = IdentifierType(id=ID_ONE, type="page")

        def sync_callback(result):
            return result

        asset = make_asset(id=ID_ONE)
        mock_driver._submitRequests.return_value = [asset]

        chain = operations.read(identifier).then(sync_callback)
        executor = ThreadPoolExecutor(max_workers=1)

        result = chain.execute(mock_driver, executor=executor)

        executor.shutdown()
        assert result is not None
        assert result is asset


# ============================================================================
# T11: Asset Identifier Tracking
# ============================================================================

class TestAssetIdentifierTracking:
    """Test that asset identifier is captured for error logging."""

    def test_identifier_tracked_from_first_operation(self, operations):
        """Chain tracks identifier from first operation node."""
        identifier = IdentifierType(id=ID_ONE, type="page")
        payload = {"page": {"name": "Updated"}}

        chain = operations.read(identifier).edit(identifier, payload)

        # Identifier should be set
        assert chain._asset_identifier is not None
        assert chain._asset_identifier == identifier

    def test_path_identifier_tracked(self, operations):
        """Works with Path objects too."""
        path = Path(
            path="/about",
            siteId=UUID(ID_ONE),
            siteName="mysite",
            asset_type="page",
        )
        payload = {"page": {"name": "Updated"}}

        chain = operations.read(path).edit(path, payload)

        assert chain._asset_identifier == path


# ============================================================================
# T12: Delete Operation in Results
# ============================================================================

class TestDeleteOperationInResults:
    """Test that delete (returning CascadeSuccess) is in results."""

    def test_delete_success_in_results(self, operations, mock_driver):
        """Delete operation returns CascadeSuccess in results."""
        identifier = IdentifierType(id=ID_ONE, type="page")

        success = CascadeSuccess()
        mock_driver._submitRequests.return_value = [success]

        chain = operations.delete(identifier)
        result = chain.execute(mock_driver)

        # Should get CascadeSuccess, not None
        assert isinstance(result, CascadeSuccess)

    def test_multiple_chains_all_in_results(self, operations, mock_driver):
        """Two chains with different ops both in results."""
        id1 = IdentifierType(id=ID_ONE, type="page")
        id2 = IdentifierType(id=ID_TWO, type="page")
        payload = {"page": {"name": "Updated"}}

        read_result = make_asset(id=ID_ONE)
        asset_result = make_asset(id=ID_ONE, name="Updated")
        success_result = CascadeSuccess()

        # One entry per operation node: read, edit, delete
        mock_driver._submitRequests.side_effect = [
            [read_result], [asset_result], [success_result],
        ]

        operations.read(id1).edit(id1, payload)
        operations.delete(id2)

        results = []
        for chain in operations._chains:
            results.append(chain.execute(mock_driver))

        assert len(results) == 2
        assert results[0] == asset_result
        assert results[1] == success_result


# ============================================================================
# T13: Empty Chain (Single Operation)
# ============================================================================

class TestSingleOperationChain:
    """Test minimal chain with just one operation."""

    def test_read_only_chain(self, operations, mock_driver):
        """Single read operation creates single-node chain."""
        identifier = IdentifierType(id=ID_ONE, type="page")

        asset = make_asset(id=ID_ONE)
        mock_driver._submitRequests.return_value = [asset]

        chain = operations.read(identifier)

        # Verify structure
        assert chain._head.node_type == "operation"
        assert chain._head.operation_type == "read"
        assert chain._head.next is None

        result = chain.execute(mock_driver)
        assert result == asset


# ============================================================================
# T14: Then Called Multiple Times (List vs Single)
# ============================================================================

class TestThenWithListAndSingle:
    """Test .then() with single callback vs list."""

    def test_then_single_then_list_then_single(self, operations):
        """Mix of single and list callbacks."""
        identifier = IdentifierType(id=ID_ONE, type="page")

        def fn1(x):
            return x

        def fn2(x):
            return x

        def fn3(x):
            return x

        def fn4(x):
            return x

        chain = (operations.read(identifier)
                 .then(fn1)
                 .then([fn2, fn3])
                 .then(fn4))

        # Count nodes
        nodes = walk(chain)

        # read + 4 callbacks
        assert len(nodes) == 5

        callback_nodes = [n for n in nodes if n.node_type == "callback"]
        assert len(callback_nodes) == 4
        assert [n.callback_fn for n in callback_nodes] == [fn1, fn2, fn3, fn4]


# ============================================================================
# Integration: Full Wrapper Test
# ============================================================================

class StubDriver:
    """Driver stand-in that answers requests without any network I/O."""

    def __init__(self, responses):
        self.base_url = "https://example.test/api/v1"
        self.responses = list(responses)
        self.batches = []
        self.eventLoop = asyncio.new_event_loop()

    def _build_url(self, *segments):
        return "/".join([self.base_url, *map(str, segments)])

    async def execute_requests(self, requests):
        self.batches.append(requests)
        return self.responses.pop(0)


class TestWrapperIntegration:
    """Test new chain architecture through CascadeWrapperBase."""

    def test_wrapper_execute_chains(self):
        """CascadeWrapperBase.submit_requests() executes chains properly."""
        id1 = IdentifierType(id=ID_ONE, type="page")
        id2 = IdentifierType(id=ID_TWO, type="page")
        payload = {"page": {"name": "Updated"}}

        asset1 = make_asset(id=ID_ONE)
        asset2 = make_asset(id=ID_TWO)
        edit_result = CascadeSuccess()

        # Chain 1 stops at its failing callback, so its edit never runs.
        driver = StubDriver([[asset1], [asset2], [edit_result]])
        logger = MagicMock(spec=OperationLogger)

        wrapper = object.__new__(CascadeWrapperBase)
        wrapper._driver = driver
        wrapper._logger = logger
        wrapper.operations = Operations(driver, _logger=logger)

        def boom(result):
            raise ValueError("bad asset")

        wrapper.operations.read(id1).then(boom).edit(id1, payload)
        wrapper.operations.read(id2).edit(id2, payload)

        try:
            results = wrapper.submit_requests()
        finally:
            driver.eventLoop.close()

        # One result per chain, in the order the chains were created
        assert len(results) == 2
        assert isinstance(results[0], ValueError)
        assert str(results[0]) == "bad asset"
        assert results[1] == edit_result

        # Chain 1's edit was skipped: read, read, edit
        assert len(driver.batches) == 3
        # Chains are cleared, so a second batch starts clean
        assert wrapper.operations._chains == []
