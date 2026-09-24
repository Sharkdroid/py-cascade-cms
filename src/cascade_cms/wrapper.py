import asyncio
import os
import sys
from concurrent.futures import Executor
from typing import Any, TypedDict, TypeVar, overload

from .cmstypes import CascadeObjects
from .driver import CascadeCMSRestDriver
from .failures import CascadeBatchError, ChainFailure, ChainResults, FailureCategory
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

    Failure handling is owned by this context manager, not the caller (see
    `submit_requests()` and `__exit__`): a `CascadeError`, a network error,
    or a library-side error stops only the chain it occurred on and is
    recorded on the returned `ChainResults`; a broken batch raises
    `CascadeBatchError`. At `__exit__`, with `exit_on_failure=True` (the
    default), any recorded failure ends the script with a non-zero exit
    code — code written after the `with` block does NOT run in that case.
    Pass `exit_on_failure=False` (e.g. when embedding this in a longer-lived
    process, such as an MCP server) to have `__exit__` never raise; results
    stay available to the caller as values via `.success`/`.failed`.

    Use as:
        with CascadeWrapperBase(env_vars, config_vars) as cascade:
            cascade.operations.read(identifier)
            results = cascade.submit_requests()
            # results.success / results.failed — no isinstance() checks needed.
    """

    def __enter__(self):
        return self

    def __init__(
        self,
        environmentVariables: EnvironmentVars,
        configurationVariables: dict[str, Any] | None,
        debug: dict[str, Any] | None = None,
        exit_on_failure: bool = True,
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
            exit_on_failure: When True (default), `__exit__` ends the script
                with a non-zero exit code if anything failed. Set False to
                embed this wrapper in a longer-lived process (e.g. an MCP
                server) where `SystemExit` must never escape — cleanup and
                the tally still run, but `__exit__` never raises.
        """
        self._exit_on_failure = exit_on_failure
        # Cumulative across every submit_requests() call in this `with`
        # block, in chain-creation order, so the deferred raise at
        # __exit__ works across multiple batches (D8, acceptance #9).
        self._callback_failures: list[ChainFailure] = []
        self._has_reportable_failure = False

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
        """Run cleanup, then decide how (or whether) to propagate.

        Cleanup (log_exit + driver.close) always runs first, exactly as
        before. After that (see D8):
          - KeyboardInterrupt/SystemExit in flight: propagate untouched.
          - CascadeBatchError in flight: with exit_on_failure, raise
            SystemExit(1) (no extra traceback — already logged); otherwise
            propagate unchanged.
          - Any other exception in flight: propagate untouched.
          - No exception, exit_on_failure False: never raise.
          - No exception, recorded callback failures: raise the first one
            (chain-creation order) unwrapped.
          - No exception, recorded CASCADE/NETWORK/LIBRARY failures: raise
            SystemExit(1).
          - No failures: return normally.
        Code after the `with` block does not run when this raises.
        """
        try:
            self._logger.log_exit()
            self._driver.close()
        except Exception as e:  # noqa: BLE001 - log cleanup failure without masking the original exception
            self._logger.log_python_error(e)

        if exc_type is not None:
            if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
                return False
            if issubclass(exc_type, CascadeBatchError):
                if self._exit_on_failure:
                    raise SystemExit(1) from None
                return False
            return False  # user-code/other exceptions propagate untouched

        if not self._exit_on_failure:
            return False

        if self._callback_failures:
            raise self._callback_failures[0].error

        if self._has_reportable_failure:
            raise SystemExit(1)

        return False

    @overload
    def submit_requests(
        self, result_type: type[T], *, executor: Executor | None = None
    ) -> ChainResults[T]: ...
    @overload
    def submit_requests(
        self, *, executor: Executor | None = None
    ) -> ChainResults[CascadeObjects]: ...

    def submit_requests(
        self, result_type: type[T] | None = None, *, executor: Executor | None = None
    ) -> ChainResults[CascadeObjects] | ChainResults[T]:
        """
        Run every registered operation chain and return one result per chain.

        Chains run concurrently, but the nodes inside a chain run strictly in
        order — each operation or callback receives the previous node's
        result. A chain stops at its first failure without affecting any
        other chain.

        Returns a `ChainResults` — a list, in **chain-creation order** (so
        `results[0]` belongs to the first chain built), with `.success` (results
        from chains that didn't fail) and `.failed` (a `ChainFailure` per
        failed chain, naming the exact step and its category) added. Raw
        values (a `CascadeError`, or the exception a callback raised) stay
        in the list unchanged — nothing is dropped or re-typed. There's no
        need for an `isinstance(result, CascadeError | Exception)` check;
        `CascadeWrapperBase.__exit__` owns whether a failure ends the
        script (see its docstring and `exit_on_failure`).

        If the batch itself breaks (not an individual chain), this raises
        `CascadeBatchError` instead of returning — it no longer swallows
        the error and returns an empty list.

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
            One entry per chain: its final result, or the error that
            stopped it — wrapped in a `ChainResults`.

        Raises:
            CascadeBatchError: The run itself broke (e.g. the driver's
                event loop failed) before chains could report their own
                results.
        """
        chains = list(self.operations._chains)
        if not chains:
            return ChainResults([], [])

        self._logger.log_batch_start()
        try:
            results = self._driver.eventLoop.run_until_complete(
                self._execute_chains(chains, executor)
            )
        except Exception as e:
            self._logger.log_python_error(e, prefix="[CASCADE-REST-CMS] ")
            self._logger.log_batch_end(0, len(chains), has_reportable_failure=True)
            self._has_reportable_failure = True
            raise CascadeBatchError(f"{type(e).__name__}: {e}") from e
        finally:
            self.operations._reset_chains()

        failures: list[ChainFailure | None] = []
        succeeded = 0
        batch_has_reportable_failure = False
        for chain, result in zip(chains, results, strict=True):
            # A8: a raw exception from asyncio.gather's return_exceptions
            # backstop, not already classified by the chain itself.
            if chain.failure is None and isinstance(result, BaseException):
                chain.failure = ChainFailure(
                    chain_index=chain._index,
                    identifier=chain._asset_identifier,
                    step=0,
                    step_name="",
                    node_type="operation",
                    category=FailureCategory.LIBRARY,
                    error=result,
                    message=str(result),
                )
            failure = chain.failure
            failures.append(failure)
            if failure is None:
                succeeded += 1
            elif failure.category is FailureCategory.CALLBACK:
                self._callback_failures.append(failure)
            else:
                batch_has_reportable_failure = True

        if batch_has_reportable_failure:
            self._has_reportable_failure = True
        self._logger.log_batch_end(
            succeeded, len(chains), has_reportable_failure=batch_has_reportable_failure
        )
        return ChainResults(list(results), failures)

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
