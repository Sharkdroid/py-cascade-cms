import asyncio
import os
import sys
from types import UnionType
from concurrent.futures import Executor

from pydantic import BaseModel
from .operations import Operations
from .driver import CascadeCMSRestDriver, CascadeObjects
from .operation_logger import OperationLogger
from typing import Dict, Any, List, Optional, Type, TypeVar, overload

T = TypeVar("T")

class CascadeWrapperBase:

    def __enter__(self):
        return self

    def __init__(
        self,
        environmentVariables: Dict[str, str],
        configurationVariables: Dict[str, Any],
        debug: Dict[str, Any] | None = None,
    ):
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
        self.operations = Operations(self._driver, logger=self._logger)

        self._logger.log_init(
            environmentVariables['CASCADE_URL'],
            os.path.basename(sys.argv[0]),
        )

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self._logger.log_exit()
            return self._driver.close()
        except Exception as e:
            self._logger.log_python_error(e)

        if exc_type is not None and not isinstance(exc_type, RuntimeWarning):
            return False  # Propagate the exception

    @overload
    def submit_requests(self, result_type: Type[T], executor: Executor | None = None) -> List[T]: ...
    @overload
    def submit_requests(self, executor: Executor | None = None) -> List[CascadeObjects]: ...
    
    def submit_requests(self, _result_type: Type[T] | None = None, executor: Executor | None = None) -> List[CascadeObjects] | List[T]:
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
        except Exception as e:
            self._logger.log_python_error(e)
            return []

    async def _execute_all_callbacks(self, results: List[CascadeObjects], executor: Executor | None = None) -> None:
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
