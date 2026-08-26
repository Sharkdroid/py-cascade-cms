import asyncio
import os
import sys
from concurrent.futures import Executor
from typing import Any, TypedDict, TypeVar, overload

from .cmstypes import CascadeError, CascadeObjects
from .driver import CascadeCMSRestDriver
from .operation_logger import OperationLogger
from .operations import OperationChain, Operations

T = TypeVar("T")


class EnvironmentVars(TypedDict):
    """Required keys for `CascadeWrapperBase`'s `environmentVariables` argument."""

    SERVER: str
    API_KEY: str
    CASCADE_URL: str


class CascadeWrapperBase:
    """Context-manager entry point tying together the logger, REST driver,
    and Operations builder for a single script/session.

    Use as:
        with CascadeWrapperBase(env_vars, config_vars) as cascade:
            cascade.operations.read(identifier)
            results = cascade.submit_requests()
    """

    def __enter__(self):
        return self

    def __init__(
        self,
        environmentVariables: EnvironmentVars,
        configurationVariables: dict[str, Any] | None,
        debug: dict[str, Any] | None = None,
    ):
        """Initialize the logger, driver, and operations builder.

        Args:
            environmentVariables: Must contain "SERVER" (label used in log
                output), "API_KEY" (Cascade bearer token), and
                "CASCADE_URL" (base URL of the Cascade instance).
            configurationVariables: kwargs forwarded to the driver's cache
                backend (`SQLiteBackend`); pass an empty dict for defaults.
            debug: Optional debug config for `OperationLogger` (verbose
                nested logging); None enables normal/minimal logging.
        """
        self._logger = OperationLogger(
            server=environmentVariables["SERVER"],
            debug_config=debug,
        )
        self._driver = CascadeCMSRestDriver(
            environmentVariables['API_KEY'],
            environmentVariables['CASCADE_URL'],
            configurationVariables,
            logger=self._logger,
        )
        self.operations = Operations(self._driver, _logger=self._logger)

        self._logger.log_init(
            environmentVariables['CASCADE_URL'],
            os.path.basename(sys.argv[0]),
        )

    def __exit__(self, exc_type, exc_value, traceback):
        """Log session exit and close the driver, then propagate exceptions.

        Any exception raised inside the `with` block (other than
        `RuntimeWarning`) is re-raised after cleanup runs.
        """
        try:
            self._logger.log_exit()
            self._driver.close()
        except Exception as e:  # noqa: BLE001 - log cleanup failure without masking the original exception
            self._logger.log_python_error(e)

        if exc_type is not None and not isinstance(exc_type, RuntimeWarning):
            return False  # Propagate the exception

    @overload
    def submit_requests(self, result_type: type[T], *, executor: Executor | None = None) -> list[T]: ...
    @overload
    def submit_requests(self, *, executor: Executor | None = None) -> list[CascadeObjects]: ...

    def submit_requests(self, result_type: type[T] | None = None, *, executor: Executor | None = None) -> list[CascadeObjects] | list[T]:
        """
        Run every registered operation chain and return one result per chain.

        Chains run concurrently, but the nodes inside a chain run strictly in
        order — each operation or callback receives the previous node's
        result. A chain stops at its first failure without affecting any
        other chain.

        Results line up with the chains **in the order they were created**,
        so `results[0]` belongs to the first chain built. Failures appear in
        that list rather than being dropped: a `CascadeError` for an API
        failure, or the exception object a callback raised. They are
        returned as values, not raised — a missed `isinstance(result,
        CascadeError | Exception)` check lets a failure flow downstream as
        if it were a normal result.

        The chain list is cleared afterwards, so callbacks registered for one
        batch never run again in the next.

        Args:
            result_type: Type hint for Pylance/mypy (e.g., submit_requests(Asset))
            executor: Optional Executor for sync callbacks.
                     Use ThreadPoolExecutor (default) for I/O-bound work.
                     Use ProcessPoolExecutor(max_workers=<cpu_count>) for CPU-bound work.

        Example (CPU-bound callbacks):
            from concurrent.futures import ProcessPoolExecutor
            from os import cpu_count

            with ProcessPoolExecutor(max_workers=cpu_count()) as executor:
                cascade.operations.read(id).then(optimize_image)
                results = cascade.submit_requests(executor=executor)

        Returns:
            One entry per chain: its final result, or the error that stopped it.
        """
        chains = list(self.operations._chains)
        if not chains:
            return []

        self._logger.log_batch_start()
        try:
            results = self._driver.eventLoop.run_until_complete(
                self._execute_chains(chains, executor)
            )
        except Exception as e:  # noqa: BLE001 - top-level entry point must not raise; log and return empty
            self._logger.log_python_error(e)
            return []
        finally:
            self.operations._reset_chains()

        succeeded = sum(1 for r in results if not isinstance(r, CascadeError | Exception))
        self._logger.log_batch_end(succeeded, len(chains))
        return results

    async def _execute_chains(
        self,
        chains: list[OperationChain],
        executor: Executor | None = None,
    ) -> list[Any]:
        """
        Run every chain concurrently and collect their results in chain order.

        `return_exceptions=True` is a backstop only: a chain already reports
        operation and callback failures as values, so an exception here means
        the chain machinery itself broke, and one broken chain must not take
        the rest of the batch down with it.

        Args:
            chains: The chains to run.
            executor: Optional Executor for sync callbacks.

        Returns:
            One entry per chain, in the order the chains were created.
        """
        results = await asyncio.gather(
            *(chain.execute_async(executor) for chain in chains),
            return_exceptions=True,
        )
        return list(results)
