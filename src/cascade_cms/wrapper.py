import asyncio
import os
import sys
from concurrent.futures import Executor
from contextvars import Token
from types import TracebackType
from typing import Any, Literal, Self, TypedDict, TypeVar, overload

from .cmstypes import CascadeObjects
from .driver import CascadeCMSRestDriver
from .utils.failures import (
    CascadeBatchError,
    ChainFailure,
    ChainResults,
    FailureCategory,
    classify_failure,
)
from .operations import OperationChain, Operations
from .utils.operation_logger import OperationLogger
from .utils.extension import ActiveRun, activate, deactivate

T = TypeVar("T")


class EnvironmentVars(TypedDict):
    """Required keys for `Cascade`'s `environmentVariables` argument."""

    SERVER: str
    API_KEY: str
    CASCADE_URL: str


class Cascade:
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
        with Cascade(env_vars) as cascade:
            cascade.operations.read(identifier)
            results = cascade.submit_requests()
            # results.success / results.failed — no isinstance() checks needed.
    """

    def __enter__(self) -> Self:
        # Route `script_log.note()` to this run for the life of the block.
        self._run_token: Token[ActiveRun | None] | None = activate(
            ActiveRun(self._logger, getattr(self, "_api_key", ""))
        )
        return self

    def __init__(
        self,
        environmentVariables: EnvironmentVars,
        debug: dict[str, Any] | None = None,
        *,
        exit_on_failure: bool = True,
        log_dir: str | os.PathLike | None = None,
    ):
        """Initialize the logger, driver, and operations builder.

        Args:
            environmentVariables: Must contain "SERVER" (label used in log
                output), "API_KEY" (Cascade bearer token), and
                "CASCADE_URL" (base URL of the Cascade instance).
            debug: Optional debug config for `OperationLogger` (verbose
                nested logging); None enables normal/minimal logging.
            exit_on_failure: When True (default), `__exit__` ends the script
                with a non-zero exit code if anything failed. Set False to
                embed this wrapper in a longer-lived process (e.g. an MCP
                server) where `SystemExit` must never escape — cleanup and
                the tally still run, but `__exit__` never raises.
            log_dir: Directory for this run's logfile (and, in debug mode,
                its request/response JSON files). Created if missing. None
                keeps the default, `./logs`. Applies in normal and debug
                mode; if the debug config also sets `log_dir`, this
                parameter wins.
        """
        self._exit_on_failure = exit_on_failure
        self._api_key = environmentVariables["API_KEY"]
        # Cumulative across every submit_requests() call in this `with`
        # block, in chain-creation order, so the deferred raise at
        # __exit__ works across multiple batches (D8, acceptance #9).
        self._callback_failures: list[ChainFailure] = []
        self._has_reportable_failure = False

        self._logger = OperationLogger(
            server=environmentVariables["SERVER"],
            debug_config=debug,
            log_dir=log_dir,
        )
        self._driver = CascadeCMSRestDriver(
            environmentVariables['API_KEY'],
            environmentVariables['CASCADE_URL'],
            logger=self._logger,
        )
        self.operations = Operations(self._driver, _logger=self._logger)

        self._logger.log_init(
            environmentVariables['CASCADE_URL'],
            os.path.basename(sys.argv[0]),
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
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
        token = getattr(self, "_run_token", None)
        if token is not None:
            self._run_token = None
            deactivate(token)

        try:
            self._logger.log_exit()
            self._driver.close()
        except Exception as e:  # noqa: BLE001 - log cleanup failure without masking the original exception
            self._logger.log_python_error(e)

        # Decide the outcome first so the logfile records it BEFORE anything
        # is raised; then release the file handle.
        exit_code, to_raise = self._decide_exit(exc_type, exc_value)
        try:
            self._logger.log_exit_code(exit_code)
        finally:
            self._logger.close()
        if isinstance(to_raise, SystemExit):
            raise to_raise from None
        if to_raise is not None:
            raise to_raise
        return False

    def _decide_exit(self, exc_type, exc_value) -> tuple[str, BaseException | None]:
        """Return the `[EXIT-CODE]` text and the exception to raise (if any).

        `exit_on_failure=False` writes `n/a` only when nothing is in flight;
        an in-flight exception keeps its normal line.
        """
        if exc_type is not None:
            if issubclass(exc_type, SystemExit):
                code = exc_value.code if exc_value is not None else None
                if code is None:
                    code = 0
                return str(code if isinstance(code, int) else 1), None
            if issubclass(exc_type, CascadeBatchError) and self._exit_on_failure:
                return "1", SystemExit(1)
            # KeyboardInterrupt, user-code and (exit_on_failure disabled)
            # batch errors propagate untouched.
            return f"1 (uncaught exception: {exc_type.__name__})", None

        if not self._exit_on_failure:
            return "n/a (exit_on_failure disabled)", None
        if self._callback_failures:
            error = self._callback_failures[0].error
            return f"1 (callback exception: {type(error).__name__})", error
        if self._has_reportable_failure:
            return "1", SystemExit(1)
        return "0", None

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
        `Cascade.__exit__` owns whether a failure ends the
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
            prefix = (
                "[NETWORK] "
                if classify_failure("operation", e) is FailureCategory.NETWORK
                else "[CASCADE-REST-CMS] "
            )
            self._logger.log_python_error(e, prefix=prefix)
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
