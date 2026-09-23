"""Failure classification and results for chain execution.

Not re-exported from `cascade_cms/__init__.py` (deliberate — import from
`cascade_cms.failures` directly). See `wrapper.CascadeWrapperBase` and
`operations.OperationChain` for where these are produced and consumed.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .cmstypes import CascadeError


class FailureCategory(Enum):
    """Where and why a chain stopped.

    CASCADE  - the API rejected the request (a `CascadeError`).
    NETWORK  - a network-layer exception at an operation step.
    LIBRARY  - any other exception at an operation step (parsing, request
               building, or a non-Exception `BaseException` from gather).
    CALLBACK - any exception raised by user callback code.
    """

    CASCADE = "CASCADE"
    NETWORK = "NETWORK"
    LIBRARY = "LIBRARY"
    CALLBACK = "CALLBACK"


def _is_network_exception(error: BaseException) -> bool:
    """True for anything D5 calls a network exception.

    Kept as the single matcher so the list is easy to extend: any exception
    class defined in the `aiohttp` package (covers `aiohttp.ClientError` and
    all its subclasses), a builtin `TimeoutError` (this includes
    `asyncio.TimeoutError`, an alias of it since Python 3.11), or a
    `ConnectionError`.
    """
    module = type(error).__module__
    return (
        module == "aiohttp"
        or module.startswith("aiohttp.")
        or isinstance(error, TimeoutError | ConnectionError)
    )


def classify_failure(node_type: str, error: Any) -> FailureCategory:
    """Classify a chain-stopping error per D5: by where it stopped, then
    by exception type — never by exception type alone.
    """
    if node_type == "callback":
        return FailureCategory.CALLBACK
    if isinstance(error, CascadeError):
        return FailureCategory.CASCADE
    if isinstance(error, BaseException) and _is_network_exception(error):
        return FailureCategory.NETWORK
    return FailureCategory.LIBRARY


@dataclass
class ChainFailure:
    """A record of where and why one chain stopped."""

    chain_index: int
    identifier: Any
    step: int
    step_name: str
    node_type: str
    category: FailureCategory
    error: Any
    message: str


class CascadeBatchError(Exception):
    """The batch itself broke (e.g. `asyncio.gather` raised before chains
    could report their own results). Always raised with `from` the
    original exception.
    """


class ChainResults[T](list[T]):
    """A list of chain results, in chain-creation order, with failures
    broken out.

    Indexing, iteration, and `len()` behave exactly like a plain list —
    `.ok` and `.failed` are added properties computed from the failure
    record recorded for each chain at execution time.
    """

    def __init__(
        self,
        results: list[T] | None = None,
        failures: list["ChainFailure | None"] | None = None,
    ) -> None:
        super().__init__(results or [])
        self._failures: list[ChainFailure | None] = list(failures or [])

    @property
    def ok(self) -> list[T]:
        """Results from chains that did not fail, typed from `result_type`."""
        return [
            result
            for result, failure in zip(self, self._failures, strict=True)
            if failure is None
        ]

    @property
    def failed(self) -> list[ChainFailure]:
        """`ChainFailure` records for every chain that failed."""
        return [failure for failure in self._failures if failure is not None]
