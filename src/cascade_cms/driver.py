import logging
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
    CascadeObjects,
    serialize_payload,
    Payloads,
    ListElements,
)
from dataclasses import dataclass, field
from pprint import pprint

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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

    async def fetch(
        self,
        session: ClientSession,
        sem: asyncio.Semaphore,
        cache: CacheHandler,
    ) -> T:
        async with sem:
            payload_bytes: bytes | None = None
            if self.payload:
                payload_bytes = serialize_payload(self.payload)

            # Cache the
            cache_key = cache.get_cache_key(self.method, self.url)
            already_cached = await cache.get_response(cache_key)
            if already_cached is not None:
                print("this request already exists! Using cache..")
                raw_data = await already_cached.read()
                parsed_response = self.parser(raw_data)
                return parsed_response._content  # type: ignore[return-value]

            async with session.request(
                self.method,
                self.url,
                data=payload_bytes,
            ) as response:
                response.raise_for_status()
                raw_data: bytes = await response.read()
                # parse raw data
                parsed_response = self.parser(raw_data)

                if parsed_response._cacheable:
                    await cache.save_response(
                        response,
                        cache_key,
                    )
                return parsed_response._content  # type: ignore[return-value]


def process_cascade_error(a, response_object: CachedResponse, debug=False):
    if not a.success or a.message != None:
        if debug:
            print("---------------------- RESPONSE INFO -------------------------")
            pprint(response_object.__dict__, indent=4)
            print("---------------------- REQUEST INFO --------------------------")
            pprint(response_object.request_info.__dict__, indent=4)


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
        verbose=False,
    ):

        self._apiKey = apiKey

        self.pending_requests: list[RequestExecutor] = []
        self.request_buffer: list[CoroutineType[Any, Any, type[BaseModel]]] = []
        self.base_url = f"{cascade_url}/api/v1"
        self.isFlushed = True

        self.setup_logging(verbose)

        # Stores response object for debugging purpose. the ClientResponse object contains request infomation as well.
        self.request_response_info: CachedResponse | None = None

        self.info("Initializing CascadeCMSDriver")

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
        sem = asyncio.Semaphore(self.MAX_REQUESTS)
        coros = [
            executor.fetch(self.session, sem, self.cache)
            for executor in self.pending_requests
        ]
        processed_results: list[CascadeObjects] = []
        for next_request in asyncio.as_completed(coros):
            result = await next_request
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
        self.info("Cleaning up resources")
        return True

    def setup_logging(self, verbose=False):
        base_logger = logging.getLogger(__class__.__name__)
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(prefix)s - %(message)s")
        handler.setFormatter(formatter)
        base_logger.addHandler(handler)
        base_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        self.prefix = {"prefix": "CascadeURLBuilder"}
        self.logger = logging.LoggerAdapter(base_logger, self.prefix)

    def info(self, msg):
        self.logger.info(msg, extra=self.prefix)

    def warn(self, msg):
        self.logger.warning(msg, extra=self.prefix)
