import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from aiohttp import ClientResponse, ClientSession
from aiohttp_client_cache import SQLiteBackend

# from aiohttp_client_cache.session import CachedSession
from aiohttp_client_cache.response import CachedResponse

from .cmstypes import (
    Payloads,
    ResponseParser,
    serialize_payload,
)
from .operation_logger import OperationLogger

T = TypeVar("T")


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
class RequestExecutor[T]:
    """Represents a single HTTP request and how to parse its response.

    Built by an `OperationChain` and executed concurrently, alongside the
    rest of its batch, via `CascadeCMSRestDriver.execute_requests()`.
    """

    url: str
    method: Literal["GET", "POST", "PUT"]
    parser: Callable[..., ResponseParser[T]] = field(
        default=lambda raw: ResponseParser(raw=raw)
    )
    payload: Payloads | None = None
    identifier: Any = None

    @property
    def log_key(self) -> str:
        """Stable key for naming this request's verbose-mode JSON files.

        Prefers the identifier's UUID hex (`IdentifierType`); falls back to
        a short hash of the URL when only a `Path` (no UUID) or no
        identifier at all is available, so a `{key}_request.json`/
        `{key}_response.json` pair still lines up for the same request.
        """
        identifier = self.identifier
        if hasattr(identifier, "get_id") and identifier.get_id:
            return str(identifier.get_id)
        return hashlib.sha1(self.url.encode()).hexdigest()[:16]

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

            # Cache the Response
            cache_key = cache.get_cache_key(self.method, self.url)
            already_cached = await cache.get_response(cache_key)
            if already_cached is not None:
                raw_data = await already_cached.read()
                if logger:
                    logger.write_response_file(self.log_key, raw_data)
                parsed_response = self.parser(raw_data)
                return parsed_response._content  # type: ignore[return-value]

            async with session.request(
                self.method,
                self.url,
                data=payload_bytes,
            ) as response:
                response.raise_for_status()
                raw_data = await response.read()
                if logger:
                    logger.write_response_file(self.log_key, raw_data)
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


def default_cache_backend() -> SQLiteBackend:
    """Build the default cache backend: SQLite, GET-only, 200s only.

    Constructed on demand rather than at module scope so that merely
    importing `cascade_cms` does not create a `./cache/` directory in the
    caller's working directory.
    """
    return SQLiteBackend(
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

        self.base_url = f"{cascade_url}/api/v1"

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

        self.cache: CacheHandler
        if backendConfig is None:
            self.cache = CacheHandler(default_cache_backend())
        else:
            self.cache = CacheHandler(SQLiteBackend(**backendConfig))

        # Shared across every chain running on this driver, so concurrency
        # stays bounded no matter how many chains are in flight at once.
        self._semaphore = asyncio.Semaphore(self.MAX_REQUESTS)

        async def _create_session():
            return ClientSession(headers=headers)

        self.session = self.eventLoop.run_until_complete(_create_session())

    def _build_url(self, *segments):
        return "/".join([self.base_url, *map(str, segments)])

    async def execute_requests(
        self,
        requests: list[RequestExecutor],
    ) -> list[Any]:
        """Run the given requests concurrently and return their results in order.

        This is the single execution core: operation chains await it directly.

        Concurrency is bounded by `MAX_REQUESTS` via a driver-wide semaphore,
        so many chains running at once still cannot exceed that ceiling.
        Unlike a plain `gather`, results line up **one-for-one with `requests`**
        in submission order, and a request that raises returns the exception
        object in its slot rather than being dropped — chains rely on that to
        report which step failed.

        Args:
            requests: The requests to execute.

        Returns:
            One entry per request, in the same order: the parsed response, a
            `CascadeError`, or the `Exception` the request raised.
        """

        async def _run(executor: RequestExecutor) -> Any:
            # Deliberate boundary (see class docstring / Section 1.10 of the
            # design doc): the driver stays chain-agnostic and only writes
            # raw request/response artifacts (via RequestExecutor.fetch's
            # own logger calls) — it has no visibility into which step, in
            # which chain, a request belongs to, so rendering a chain's
            # pipeline line or its `v`/`!ERROR:` alignment is OperationChain's
            # job (execute/execute_async), done after this returns a value.
            try:
                return await executor.fetch(
                    self.session, self._semaphore, self.cache, self._logger
                )
            except Exception as exc:  # noqa: BLE001 - surfaced as a value, see docstring
                return exc

        return list(await asyncio.gather(*(_run(request) for request in requests)))

    def _submitRequests(
        self,
        requests: list[RequestExecutor],
    ) -> list[Any]:
        """Run the event loop to completion for a batch of requests.

        Blocks until every request finishes.

        Args:
            requests: The requests to execute.

        Returns:
            One entry per request, in submission order.
        """
        return self.eventLoop.run_until_complete(self.execute_requests(requests))

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
