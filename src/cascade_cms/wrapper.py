import asyncio
import os
import sys
from concurrent.futures import Executor
from typing import Any, TypeVar, overload

from .driver import CascadeCMSRestDriver, CascadeObjects
from .operation_logger import OperationLogger
from .operations import Operations

T = TypeVar("T")

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
        environmentVariables: dict[str, str],
        configurationVariables: dict[str, Any],
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
            return self._driver.close()
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
        Submit all pending requests and execute registered callbacks on results.
        
        Callbacks are invoked sequentially per result:
            result1 → callback1 → callback2
            result2 → callback1 → callback2
            ...
        
        Args:
            _result_type: Type hint for Pylance/mypy (e.g., submit_requests(Asset))
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
            List of results from all requests. Callbacks execute during this call
            but do not modify the returned results (unless they explicitly do).
        """
        self._logger.log_running(os.path.basename(sys.argv[0]))
        try:
            # Step 1: Execute all HTTP requests
            results = self._driver._submitRequests()

            # Step 2: If callbacks are registered, execute them on each result
            if self.operations._callbacks:
                # Use the driver's existing event loop to run callbacks
                self._driver.eventLoop.run_until_complete(
                    self._execute_all_callbacks(results, executor)
                )

            return results
        except Exception as e:  # noqa: BLE001 - top-level entry point must not raise; log and return empty
            self._logger.log_python_error(e)
            return []

    async def _execute_all_callbacks(self, results: list[CascadeObjects], executor: Executor | None = None) -> None:
        """
        Execute registered callbacks on all results sequentially per result.
        
        Uses asyncio.gather to allow concurrent processing of different results
        (if callbacks are async), while keeping sequential callback order per result.
        
        Args:
            results: List of result objects from API requests.
            executor: Optional Executor for sync callbacks.
                     None (default) uses ThreadPoolExecutor.
                     ProcessPoolExecutor for CPU-bound work.
        """
        if not results:
            return
        
        # Create a task for each result
        tasks = [
            self.operations._execute_callbacks_on_result(result, executor)
            for result in results
        ]
        
        # Run all tasks concurrently, capturing any exceptions
        # return_exceptions=True prevents one failing callback from crashing the batch
        await asyncio.gather(*tasks, return_exceptions=False)
