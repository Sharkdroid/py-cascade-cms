from types import CoroutineType
from typing import Any, Dict, Callable, Generic, Tuple, TypeVar

T = TypeVar("T")

# from aiohttp_client_cache.session import CachedSession
from aiohttp import ClientSession, ClientResponse
from aiohttp_client_cache.response import CachedResponse
from aiohttp_client_cache import SQLiteBackend
import asyncio
from .cmstypes import (
    Literal,
    ResponseParser,
    BaseModel,
    CascadeError,
    CascadeObjects,
    serialize_payload,
    Payloads,
    ListElements,
)
from .operation_logger import OperationLogger
from dataclasses import dataclass, field


@dataclass
class CacheHandler:
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


DEFAULT_CACHECONFIG: SQLiteBackend = SQLiteBackend(
    cache_name=f"./cache/cache.sqlite",
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
        backendConfig: Dict[str, Any] | None,
        logger: OperationLogger | None = None,
    ):

        self._apiKey = apiKey
        self._logger = logger

        self.pending_requests: list[RequestExecutor] = []
        self.request_buffer: list[CoroutineType[Any, Any, type[BaseModel]]] = []
        self.base_url = f"{cascade_url}/api/v1"
        self.isFlushed = True

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
        if self._logger:
            self._logger.set_total(len(self.pending_requests))

        sem = asyncio.Semaphore(self.MAX_REQUESTS)

        async def _run(executor: RequestExecutor) -> CascadeObjects | None:
            try:
                result = await executor.fetch(
                    self.session, sem, self.cache, self._logger
                )
            except Exception as exc:
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

    """
    initializes headers for the connected session and acculmate all request tasks
    """

    def _submitRequests(self) -> list[CascadeObjects]:
        res = self.eventLoop.run_until_complete(self.process_executors())
        self.pending_requests.clear()
        return res

    def close(self):
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
