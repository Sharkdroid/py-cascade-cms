from collections.abc import Callable
from types import CoroutineType
from typing import Any, Generic, Literal, TypeVar

T = TypeVar("T")

# from aiohttp_client_cache.session import CachedSession
import asyncio
from dataclasses import dataclass, field

from aiohttp import ClientResponse, ClientSession
from aiohttp_client_cache import SQLiteBackend
from aiohttp_client_cache.response import CachedResponse

from .cmstypes import (
    BaseModel,
    CascadeError,
    CascadeObjects,
    Payloads,
    ResponseParser,
    serialize_payload,
)
from .operation_logger import OperationLogger


@dataclass
class CacheHandler:
    """Thin wrapper around an aiohttp-client-cache SQLite backend.

    Only GET responses are ever cached (enforced by the backend's
    `allowed_methods` config) so repeated reads skip the network,
    while POST/PUT requests always hit the server.
    """

    cache_db: SQLiteBackend

    async def get_response(
        self,
        key: str,
    ) -> CachedResponse | None:
        return await self.cache_db.get_response(key)

    def get_cache_key(
        self,
        method: str,
        url: str,
    ) -> str:
        """Returns a response in cach if it exists"""
        return self.cache_db.create_key(method, url)

    async def save_response(
        self,
        response: ClientResponse,
        cache_key: str,
    ):
        """Check to see if response is cacheable"""
        return await self.cache_db.save_response(response, cache_key)


@dataclass
class RequestExecutor(Generic[T]):
    """Represents a single queued HTTP request and how to parse its response.

    Instances accumulate in `CascadeCMSRestDriver.pending_requests` and are
    all executed concurrently by `process_executors()`.
    """

    url: str
    method: Literal["GET", "POST", "PUT"]
    parser: Callable[..., ResponseParser[T]] = field(
        default=lambda raw: ResponseParser(raw=raw)
    )
    payload: Payloads | None = None
    identifier: Any = None

    async def fetch(
        self,
        session: ClientSession,
        sem: asyncio.Semaphore,
        cache: CacheHandler,
        logger: "OperationLogger | None" = None,
    ) -> T:
        """Execute this request, using the cache for GETs when possible.

        Checks the cache first; on a miss, performs the network request,
        parses the response, and stores it in the cache if parsing marked
        it cacheable. Concurrency across requests is bounded by `sem`.

        Args:
            session: Shared aiohttp session to issue the request on.
            sem: Semaphore limiting concurrent in-flight requests.
            cache: Cache handler used to short-circuit repeated GETs.
            logger: Optional logger for recording request/response detail.

        Returns:
            The parsed response content (an asset, list, error, etc.).
        """
        async with sem:
            payload_bytes: bytes | None = None
            if self.payload:
                payload_bytes = serialize_payload(self.payload)

            # Cache the
            cache_key = cache.get_cache_key(self.method, self.url)
            already_cached = await cache.get_response(cache_key)
            if already_cached is not None:
                raw_data = await already_cached.read()
                if logger:
                    logger.log_response(raw_data)
                parsed_response = self.parser(raw_data)
                return parsed_response._content  # type: ignore[return-value]

            async with session.request(
                self.method,
                self.url,
                data=payload_bytes,
            ) as response:
                response.raise_for_status()
                raw_data: bytes = await response.read()
                if logger:
                    logger.log_response(raw_data)
                    logger.log_network_headers(
                        dict(response.request_info.headers),
                        dict(response.headers),
                    )
                # parse raw data
                parsed_response = self.parser(raw_data)

                if parsed_response._cacheable:
                    await cache.save_response(
                        response,
                        cache_key,
                    )
                return parsed_response._content  # type: ignore[return-value]


# Default cache: SQLite-backed, GET-only, only caches 200 responses.
DEFAULT_CACHECONFIG: SQLiteBackend = SQLiteBackend(
    cache_name="./cache/cache.sqlite",
    allowed_codes=(200,),
    allowed_methods=("GET",),
)


class CascadeCMSRestDriver:
    """
    URL builder and executor for Cascade CMS 8 REST API.
    Inherits URL-building from CascadeCMSURLBuilder and applies an optional parser function to each response.
    """

    MAX_REQUESTS = 50  # limits the requests to 50 requests per submission.

    def __init__(
        self,
        apiKey: str,
        cascade_url: str,
        backendConfig: dict[str, Any] | None,
        logger: OperationLogger | None = None,
    ):
        """Set up the aiohttp session, event loop, and cache for this driver.

        Creates a dedicated event loop that is reused for the lifetime of
        this driver instance (rather than one per call), so all requests
        and cache I/O run on the same loop.

        Args:
            apiKey: Cascade CMS API bearer token.
            cascade_url: Base URL of the Cascade CMS instance (without
                the `/api/v1` suffix, which is appended automatically).
            backendConfig: kwargs forwarded to `SQLiteBackend` to override
                the default cache config, or None to use the default.
            logger: Optional logger for recording operations/errors.
        """

        self._apiKey = apiKey
        self._logger = logger

        self.pending_requests: list[RequestExecutor] = []
        self.request_buffer: list[CoroutineType[Any, Any, type[BaseModel]]] = []
        self.base_url = f"{cascade_url}/api/v1"
        self.isFlushed = True  # NOTE: appears unused elsewhere in this codebase

        # Stores response object for debugging purpose. the ClientResponse object contains request infomation as well.
        self.request_response_info: CachedResponse | None = None

        # intializing event loop, for re-use for the rest of the session.
        self.eventLoop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.eventLoop)

        headers = {
            "Authorization": f"Bearer {self._apiKey}",
            "Cache-Control": "private",
            "Content-Type": "application/json;charset=UTF-8",
        }

        if backendConfig is None:
            self.cache: CacheHandler = CacheHandler(DEFAULT_CACHECONFIG)
        else:
            self.cache: CacheHandler = CacheHandler(SQLiteBackend(**backendConfig))

        async def _create_session():
            return ClientSession(headers=headers)

        self.session = self.eventLoop.run_until_complete(_create_session())

    def _build_url(self, *segments):
        return "/".join([self.base_url, *map(str, segments)])

    async def process_executors(self) -> list[CascadeObjects]:
        """Run all pending requests concurrently and collect their results.

        Concurrency is bounded by `MAX_REQUESTS` via a semaphore. Requests
        that raise a Python exception or return a `CascadeError` are logged
        and excluded from the returned list, so only successfully parsed
        results are returned (order is completion order, not submission
        order, since results are gathered via `asyncio.as_completed`).

        Returns:
            The list of successfully parsed response objects.
        """
        if self._logger:
            self._logger.set_total(len(self.pending_requests))

        sem = asyncio.Semaphore(self.MAX_REQUESTS)

        async def _run(executor: RequestExecutor) -> CascadeObjects | None:
            try:
                result = await executor.fetch(
                    self.session, sem, self.cache, self._logger
                )
            except Exception as exc:  # noqa: BLE001 - isolate one request's failure from the batch
                if self._logger:
                    self._logger.log_python_error(exc)
                    self._logger.log_progress(failed=True)
                return None

            failed = isinstance(result, CascadeError)
            if failed and self._logger:
                self._logger.log_cascade_error(result.message, executor.identifier)
            if self._logger:
                self._logger.log_progress(failed=failed)
            return result

        coros = [_run(executor) for executor in self.pending_requests]
        processed_results: list[CascadeObjects] = []
        for next_request in asyncio.as_completed(coros):
            result = await next_request
            if result is not None:
                processed_results.append(result)
        return processed_results

    def _submitRequests(self) -> list[CascadeObjects]:
        """Run the event loop to completion for all pending requests.

        Blocks until every queued request finishes, then clears
        `pending_requests` so the driver is ready for the next batch.
        """
        res = self.eventLoop.run_until_complete(self.process_executors())
        self.pending_requests.clear()
        return res

    def close(self):
        """Tear down the aiohttp session, cache DB, and event loop."""
        if getattr(self, "session", None) is not None and not getattr(
            self.session, "closed", False
        ):
            self.eventLoop.run_until_complete(self.session.close())

        if (
            getattr(self, "cache", None) is not None
            and getattr(self.cache, "cache_db", None) is not None
        ):
            self.eventLoop.run_until_complete(self.cache.cache_db.close())

        self.eventLoop.close()
        return True
