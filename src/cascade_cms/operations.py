import asyncio
import os
import traceback
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal, Self

from .cmstypes import (
    Asset,
    AssetLogIdentifier,
    CascadeError,
    Comment,
    IdentifierType,
    ListElements,
    Message,
    NewAsset,
    Path,
    SearchInformation,
    SimplePayload,
    SiteCopyParameter,
    accessRightsInformationPayload,
    auditParameters,
    copyParameters,
    deleteParameters,
    edit_log_identifier_from_asset,
    moveParameters,
    parse_access_rights,
    parse_assets,
    parse_checked_out_asset,
    parse_create_asset,
    parse_list_elements,
    parse_payloads,
    parse_success,
    parse_workflow_information,
    parse_workflow_settings,
    preference,
    publishInformation,
    resolve_identifier,
    serialize_payload,
    set_checkedout,
    workflowSettingsPayload,
    workflowTransitionInformation,
)
from .driver import CascadeCMSRestDriver, RequestExecutor
from .operation_logger import ChainLineBuilder, OperationLogger

NodeType = Literal["operation", "callback"]
HTTPMethod = Literal["GET", "POST", "PUT"]


@dataclass
class Node:
    """One unit of work in an `OperationChain`: a request or a callback.

    Nodes form a singly linked list via `next`. An operation node carries
    its prepared requests in `operation_data`; a callback node carries the
    function to run on the previous node's result.
    """

    node_type: NodeType
    operation_type: str | None = None
    callback_fn: Callable[[Any], Any] | None = None
    operation_data: dict[str, Any] | None = None
    next: "Node | None" = None
    input: Any = None
    output: Any = None
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def operation(cls, op_type: str, op_data: dict[str, Any]) -> "Node":
        """Build an operation node for `op_type` from prepared request data."""
        return cls(node_type="operation", operation_type=op_type, operation_data=op_data)

    @classmethod
    def callback(cls, fn: Callable[[Any], Any]) -> "Node":
        """Build a callback node wrapping `fn`."""
        return cls(node_type="callback", callback_fn=fn)

    @property
    def name(self) -> str:
        """Human-readable label for logs: the endpoint or the callback name."""
        if self.node_type == "operation":
            return self.operation_type or "operation"
        return getattr(self.callback_fn, "__name__", repr(self.callback_fn))


@dataclass
class OperationChain:
    """A sequence of operations and callbacks executed in order for one asset.

    Every `cascade.operations.<op>()` call starts a chain; `.then()` and any
    further operation calls append to *that* chain. Nodes run strictly in
    order, each receiving the previous node's result, and the chain stops at
    the first failure — so a result is always paired with the operation that
    produced it, and a failure names the exact step it happened at.

    Chains are independent: one failing does not affect any other, and
    `submit_requests()` runs them all concurrently.

    Example:
        cascade.operations.read(identifier).then(rewrite).edit(build_payload)
    """

    _driver: CascadeCMSRestDriver
    _head: Node | None = None
    _current: Node | None = None
    _logger: OperationLogger | None = None
    _asset_identifier: IdentifierType | Path | AssetLogIdentifier | None = None
    _index: int = 0
    _line: ChainLineBuilder = field(default_factory=ChainLineBuilder)

    # ------------------------------------------------------------------ #
    # Chain building                                                       #
    # ------------------------------------------------------------------ #

    def _append(self, node: Node) -> Self:
        """Link `node` onto the tail of the chain."""
        if self._head is None:
            self._head = node
        else:
            assert self._current is not None
            self._current.next = node
        self._current = node

        if node.node_type == "operation" and self._asset_identifier is None:
            data = node.operation_data or {}
            self._asset_identifier = data.get("identifier")
        return self

    def _add_operation(
        self,
        op_type: str,
        requests: list[RequestExecutor] | None,
        *,
        identifier: Any = None,
        payload: Any = None,
        parser: Any = None,
        multi: bool = False,
        builder: Callable[[Any], list[RequestExecutor]] | None = None,
    ) -> Self:
        """Append an operation node built from already-prepared requests.

        `requests` is None only when the requests cannot be built until the
        chain runs (a callable payload), in which case `builder` produces
        them from the previous node's result.

        `multi=True` marks a node whose request list is actually N
        independent requests batched together (`create`'s or `edit`'s
        multi-asset payload) so `_resolve_operation_result` keeps the whole
        list as the result instead of unwrapping to one value and stopping
        on the first error. Identifier-addressed operations (read/delete/
        copy/...) never set this — a list of identifiers fans out into one
        independent chain per identifier instead (see Approach A below).
        """
        return self._append(
            Node.operation(
                op_type,
                {
                    "requests": requests,
                    "builder": builder,
                    "identifier": identifier,
                    "payload": payload,
                    "parser": parser,
                    "multi": multi,
                },
            )
        )

    # ------------------------------------------------------------------ #
    # Approach A — per-item independent chains for list-of-identifiers    #
    #                                                                      #
    # SCOPE (locked, do not re-litigate): every identifier-addressed      #
    # operation — read, delete, copy, move, publish, checkIn, checkOut,   #
    # listSubscribers, readAccessRights — fans a list of identifiers out  #
    # into one independent `OperationChain` per identifier (see           #
    # `Operations._new_chains_for` / `ChainGroup` below), uniformly,      #
    # rather than batching N requests into a single node. This is why     #
    # `_identifier_operation` below only ever handles ONE identifier:     #
    # the fan-out already happened one level up, in `Operations`, before  #
    # a single-identifier chain method is ever called.                    #
    # ------------------------------------------------------------------ #

    def _identifier_operation(
        self,
        *,
        op_name: str,
        method: HTTPMethod,
        identifier: Any,
        parser: Any,
        payload: Any = None,
        checkout_ledger: bool = False,
    ) -> Self:
        """Append a node for an endpoint addressed as `<op>/{type}/{id-or-path}`.

        Always a single identifier, single request, single node — a list of
        identifiers is handled by `Operations` fanning out into one chain
        per identifier before this ever runs (see Approach A above).

        No separate `log_name` parameter anymore — the pipeline-line label
        (pass 3) is derived from `op_name.upper()` at execution time, which
        was always identical to the old build-time `log_name` value.
        """
        segments = resolve_identifier(identifier)
        if checkout_ledger:
            set_checkedout("/".join(segments))
        url = self._driver._build_url(op_name, *segments)
        request = RequestExecutor(url, method, parser, payload=payload, identifier=identifier)

        return self._add_operation(
            op_name, [request], identifier=identifier, payload=payload, parser=parser
        )

    def then(
        self,
        callback_fn: Callable[[Any], Any] | list[Callable[[Any], Any]],
    ) -> Self:
        """Append one callback node per function, run in the order given.

        Each callback receives the previous node's result and its return
        value becomes the next node's input. A callback returning None is
        treated as a side effect: the previous result carries through
        unchanged. Sync and async callbacks are both supported.

        Args:
            callback_fn: A callable, or a list of callables to append in order.

        Returns:
            This chain, for further chaining.
        """
        callbacks = callback_fn if isinstance(callback_fn, list) else [callback_fn]
        for fn in callbacks:
            self._append(Node.callback(fn))
        return self

    # ------------------------------------------------------------------ #
    # Operations                                                           #
    # ------------------------------------------------------------------ #

    def read(
        self,
        identifier: IdentifierType | Path,
        parser=parse_assets,
    ) -> Self:
        """Append a GET `read/{type}/{id-or-path}` step for an asset."""
        return self._identifier_operation(
            op_name="read",
            method="GET",
            identifier=identifier,
            parser=parser,
        )

    def delete(
        self,
        identifier: IdentifierType | Path,
        payload: deleteParameters | None = None,
        parser=parse_success,
    ) -> Self:
        """Append a POST `delete/{type}/{id-or-path}` step for an asset."""
        return self._identifier_operation(
            op_name="delete",
            method="POST",
            identifier=identifier,
            payload=payload,
            parser=parser,
        )

    def create(self, payload: list[NewAsset] | NewAsset, parser=None) -> Self:
        """Append a POST `create` step for one or more new assets."""
        multi = isinstance(payload, list)
        assets: list[NewAsset] = payload if isinstance(payload, list) else [payload]

        requests: list[RequestExecutor] = []
        for single_asset in assets:
            url = self._driver._build_url("create")
            # Bind asset_type NOW while we have it
            bound_parser = partial(parse_create_asset, pass_type=single_asset.asset_type)
            requests.append(
                RequestExecutor(
                    url,
                    "POST",
                    payload=single_asset,
                    parser=bound_parser,  # This now only expects (raw)
                )
            )

        return self._add_operation(
            "create", requests, payload=payload, parser=parser, multi=multi
        )

    def edit(
        self,
        payload: Asset | list[Asset] | Callable[[Any], Asset | list[Asset]],
        parser=parse_success,
    ) -> Self:
        """Append a POST `edit` step saving one or more modified assets.

        The `edit` endpoint is addressed by the payload rather than an
        explicit identifier: each asset's own id/path/site fields are used
        to build its per-request identifier (see `edit_log_identifier_from_asset`),
        and the chain's own logging identifier is captured from it too when
        this is the chain's only node (see `_build_edit_requests`).

        `payload` may be a callable, in which case it is invoked with the
        previous node's result when the chain runs — that is how
        `read → then(transform) → edit` writes back the transformed asset.

        Args:
            payload: The asset(s) to save, or a callable producing them from
                the previous node's result.
            parser: Response parser, defaults to `parse_success`.
        """
        if callable(payload):
            builder = partial(self._build_edit_requests, parser=parser, resolver=payload)
            return self._add_operation(
                "edit",
                None,
                payload=payload,
                parser=parser,
                multi=False,
                builder=builder,
            )

        multi = isinstance(payload, list)
        requests = self._build_edit_requests(None, parser=parser, resolver=payload)
        return self._add_operation(
            "edit",
            requests,
            payload=payload,
            parser=parser,
            multi=multi,
        )

    def _edit_identifier(self, asset: Asset) -> AssetLogIdentifier:
        """Resolve the identifier an `edit` request/chain reports itself under.

        Raises clearly here (rather than letting `edit_log_identifier_from_asset`'s
        bare `UUID(None)` failure surface) because a missing id on this
        specific call path means a synthetic create()-style `Asset` was
        routed through `edit()` by mistake, not a generally-tolerable gap.
        """
        try:
            asset.get("id")
        except KeyError:
            path = asset._data.get("path", "<unknown>")
            raise ValueError(
                f"Cannot edit asset at path={path!r} — it has no "
                "'id'. This usually means an Asset built for create() was "
                "routed through edit() by mistake."
            )
        return edit_log_identifier_from_asset(asset)

    def _build_edit_requests(
        self,
        previous: Any,
        *,
        parser: Any,
        resolver: Any,
    ) -> list[RequestExecutor]:
        """Build the edit requests, resolving a callable payload if needed."""
        payload = resolver(previous) if callable(resolver) else resolver
        assets = payload if isinstance(payload, list) else [payload]

        if self._asset_identifier is None and assets:
            # Lazy capture: a standalone `edit(asset)` chain (no prior read)
            # has no earlier operation node for `_append` to pull an
            # identifier from, since edit() itself no longer takes one — so
            # derive the chain's logging identifier from the asset being
            # saved once we actually have it in hand, using the first asset
            # as representative for a batch payload.
            self._asset_identifier = self._edit_identifier(assets[0])

        requests: list[RequestExecutor] = []
        for single_asset in assets:
            url = self._driver._build_url("edit")
            identifier = self._edit_identifier(single_asset)
            requests.append(
                RequestExecutor(
                    url, "POST", parser, payload=single_asset, identifier=identifier
                )
            )
        return requests

    def copy(
        self,
        identifier: IdentifierType | Path,
        payload: copyParameters,
        parser=parse_success,
    ) -> Self:
        """Append a POST `copy/{type}/{id-or-path}` step for an asset."""
        return self._identifier_operation(
            op_name="copy",
            method="POST",
            identifier=identifier,
            payload=payload,
            parser=parser,
        )

    def move(
        self,
        identifier: IdentifierType | Path,
        payload: moveParameters,
        parser=parse_success,
    ) -> Self:
        """Append a POST `move/{type}/{id-or-path}` step to move/rename an asset."""
        return self._identifier_operation(
            op_name="move",
            method="POST",
            identifier=identifier,
            payload=payload,
            parser=parser,
        )

    def publish(
        self,
        identifier: IdentifierType | Path,
        payload: None | publishInformation = None,
        parser=parse_success,
    ) -> Self:
        """Append a POST `publish/{type}/{id-or-path}` step for an asset."""
        return self._identifier_operation(
            op_name="publish",
            method="POST",
            identifier=identifier,
            payload=payload,
            parser=parser,
        )

    def search(
        self,
        payload: SearchInformation,
        parser=parse_list_elements,
    ) -> Self:
        """Append a POST `search` step with the given search criteria."""
        assert isinstance(payload, SearchInformation)
        url = self._driver._build_url("search")
        request = RequestExecutor[ListElements](url, "POST", parser, payload=payload)
        return self._add_operation("search", [request], payload=payload, parser=parser)

    # -------Asset Controls-------
    def checkIn(
        self,
        identifier: IdentifierType | Path,
        payload: Comment,
        parser=parse_success,
    ) -> Self:
        """Append a POST `checkIn/...` step, toggling the local checkout ledger."""
        return self._identifier_operation(
            op_name="checkIn",
            method="POST",
            identifier=identifier,
            payload=payload,
            parser=parser,
            checkout_ledger=True,
        )

    def checkOut(
        self,
        identifier: IdentifierType | Path,
        parser=parse_checked_out_asset,
    ) -> Self:
        """Append a POST `checkOut/...` step, toggling the local checkout ledger."""
        return self._identifier_operation(
            op_name="checkOut",
            method="POST",
            identifier=identifier,
            parser=parser,
            checkout_ledger=True,
        )

    def listSites(self, parser=parse_list_elements) -> Self:
        """Append a GET `listSites` step listing all sites."""
        url = self._driver._build_url("listSites")
        request = RequestExecutor[ListElements](url, "GET", parser)
        return self._add_operation("listSites", [request], parser=parser)

    def readAudits(
        self,
        payload: auditParameters,
        parser=parse_list_elements,
    ) -> Self:
        """Append a GET `readAudits` step for audit entries matching the criteria."""
        url = self._driver._build_url("readAudits")
        request = RequestExecutor[ListElements](url, "GET", parser, payload=payload)
        return self._add_operation(
            "readAudits",
            [request],
            identifier=payload.by_identifier,
            payload=payload,
            parser=parser,
        )

    def listSubscribers(
        self,
        identifier: IdentifierType | Path,
        parser=parse_list_elements,
    ) -> Self:
        """Append a GET `listSubscribers/{type}/{id-or-path}` step for an asset."""
        return self._identifier_operation(
            op_name="listSubscribers",
            method="GET",
            identifier=identifier,
            parser=parser,
        )

    def siteCopy(
        self,
        payload: SiteCopyParameter,
        parser=parse_success,
    ) -> Self:
        """Append a POST `siteCopy` step copying an entire site."""
        url = self._driver._build_url("siteCopy")
        request = RequestExecutor(url, "POST", parser, payload=payload)
        return self._add_operation("siteCopy", [request], payload=payload, parser=parser)

    def readAccessRights(
        self,
        identifier: IdentifierType | Path,
        parser=parse_access_rights,
    ) -> Self:
        """Append a GET `readAccessRights/{type}/{id-or-path}` step for an asset."""
        return self._identifier_operation(
            op_name="readAccessRights",
            method="GET",
            identifier=identifier,
            parser=parser,
        )

    def editAccessRights(
        self,
        payload: accessRightsInformationPayload,
    ) -> Self:
        """Append a POST `editAccessRights` step updating an asset's ACL."""
        url = self._driver._build_url("editAccessRights")
        request = RequestExecutor(url, "POST", parse_success, payload=payload)
        return self._add_operation(
            "editAccessRights", [request], payload=payload, parser=parse_success
        )

    def readWorkflowSettings(
        self,
        identifier: IdentifierType | Path,
        parser=parse_workflow_settings,
    ) -> Self:
        """Append a GET `readWorkflowSettings/{type}/{id-or-path}` step."""
        return self._identifier_operation(
            op_name="readWorkflowSettings",
            method="GET",
            identifier=identifier,
            parser=parser,
        )

    def editWorkflowSettings(
        self,
        payload: workflowSettingsPayload,
        parser=parse_success,
    ) -> Self:
        """Append a POST `editWorkflowSettings/{type}/{id}` step for an asset."""
        id_fields = payload.body["identifier"]
        url = self._driver._build_url(
            "editWorkflowSettings",
            id_fields.get_type,
            id_fields.get_id,
        )
        request = RequestExecutor(
            url, "POST", parser, payload=payload, identifier=id_fields
        )
        return self._add_operation(
            "editWorkflowSettings",
            [request],
            identifier=id_fields,
            payload=payload,
            parser=parser,
        )

    def listMessages(self, parser=parse_list_elements) -> Self:
        """Append a GET `listMessages` step listing inbox messages."""
        url = self._driver._build_url("listMessages")
        request = RequestExecutor[ListElements](url, "GET", parser)
        return self._add_operation("listMessages", [request], parser=parser)

    def markMessage(self, message: Message) -> Self:
        """Append a POST `markMessage` step setting a message's read state."""
        url = self._driver._build_url(
            "markMessage", message.__class__.__name__, message.m_id
        )
        request = RequestExecutor(url, "POST", parse_success, payload=message)
        return self._add_operation(
            "markMessage", [request], payload=message, parser=parse_success
        )

    def deleteMessage(self, message: Message) -> Self:
        """Append a POST `deleteMessage` step deleting a message."""
        url = self._driver._build_url(
            "deleteMessage", message.__class__.__name__, message.m_id
        )
        request = RequestExecutor(url, "POST", parse_success)
        return self._add_operation("deleteMessage", [request], parser=parse_success)

    def readPreferences(self, parser=parse_payloads) -> Self:
        """Append a GET `readPreferences` step for the current user."""
        url = self._driver._build_url("readPreferences")
        request = RequestExecutor[SimplePayload](url, "GET", parser)
        return self._add_operation("readPreferences", [request], parser=parser)

    def editPreference(self, payload: preference) -> Self:
        """Append a POST `editPreference` step updating a user preference."""
        url = self._driver._build_url("editPreference")
        request = RequestExecutor(url, "POST", parse_success, payload=payload)
        return self._add_operation(
            "editPreference", [request], payload=payload, parser=parse_success
        )

    def readWorkflowInformation(
        self,
        identifier: IdentifierType | Path,
        parser=parse_workflow_information,
    ) -> Self:
        """Append a GET `readWorkflowInformation/{type}/{id-or-path}` step."""
        return self._identifier_operation(
            op_name="readWorkflowInformation",
            method="GET",
            identifier=identifier,
            parser=parser,
        )

    def performWorkflowTransition(
        self,
        identifier: IdentifierType | Path,
        payload: workflowTransitionInformation,
        parser=parse_success,
    ) -> Self:
        """Append a POST `performWorkflowTransition/...` step advancing a workflow."""
        return self._identifier_operation(
            op_name="performWorkflowTransition",
            method="POST",
            identifier=identifier,
            payload=payload,
            parser=parser,
        )

    # ------------------------------------------------------------------ #
    # Execution                                                            #
    # ------------------------------------------------------------------ #

    def _node_requests(self, node: Node, previous: Any) -> list[RequestExecutor]:
        """The requests for an operation node, built now if they were deferred."""
        data = node.operation_data or {}
        requests = data.get("requests")
        if requests is None:
            builder = data["builder"]
            requests = builder(previous)
        return requests

    def _resolve_operation_result(self, node: Node, raw: Any) -> tuple[Any, bool]:
        """Turn a driver response into this node's result and a stop flag.

        A node built from a list of identifiers keeps its list shape (errors
        included) and never stops the chain — the caller's callback decides
        what to do with partial failures. A single-request node stops the
        chain when its result is a `CascadeError` or an exception.
        """
        data = node.operation_data or {}
        results = list(raw) if isinstance(raw, list) else [raw]

        if data.get("multi"):
            return results, False

        value = results[0] if results else None
        return value, isinstance(value, CascadeError | Exception)

    def _run_callback_sync(
        self,
        node: Node,
        previous: Any,
        driver: CascadeCMSRestDriver,
        executor: Executor | None,
    ) -> Any:
        """Invoke a callback node from synchronous code."""
        fn = node.callback_fn
        assert fn is not None
        if asyncio.iscoroutinefunction(fn):
            value = driver.eventLoop.run_until_complete(fn(previous))
        elif executor is not None:
            value = executor.submit(fn, previous).result()
        else:
            value = fn(previous)
        # A callback that returns nothing is a side effect: keep the input.
        return previous if value is None else value

    async def _run_callback_async(
        self,
        node: Node,
        previous: Any,
        executor: Executor | None,
    ) -> Any:
        """Invoke a callback node from the async batch path."""
        fn = node.callback_fn
        assert fn is not None
        if asyncio.iscoroutinefunction(fn):
            value = await fn(previous)
        else:
            # Sync callbacks run off the event loop so they cannot block it.
            loop = asyncio.get_running_loop()
            value = await loop.run_in_executor(executor, fn, previous)
        return previous if value is None else value

    def _start_line_if_needed(self) -> None:
        """Resolve the chain-line's `(uuid_or_path, asset_type)` prefix, once.

        Deferred rather than called unconditionally up front because a
        standalone `edit(asset)` chain doesn't know `_asset_identifier`
        until its (only) node's requests are built (see
        `_build_edit_requests`'s lazy capture) — calling this again after
        that population is a no-op for every other chain shape, where
        `_asset_identifier` is already known from the first `read`/etc. node.
        """
        if not self._line.identifier:
            self._line.start(self._asset_identifier)

    def _log_requests(self, requests: list[RequestExecutor]) -> None:
        """Log `[METHOD] URL` for every request in an operation node, before
        it runs — one call per server-touching request (not just the first),
        matching a `create`/`edit` batch node's multiple requests as well as
        the common single-request case.

        In verbose mode, also writes the request payload to its own file and
        references it by name; `log_request_detail` itself never inlines a
        payload, in any mode.
        """
        if self._logger is None:
            return
        for request in requests:
            payload_ref = None
            if self._logger.is_debug and request.payload is not None:
                key = request.log_key
                payload_ref = f"{key}_request.json"
                self._logger.write_request_file(key, serialize_payload(request.payload))
            self._logger.log_request_detail(request.method, request.url, payload_ref)

    @staticmethod
    def _error_location(error: Any) -> tuple[str, str, int]:
        """`(message, filename, line)` for `render_error`'s `!ERROR:` line.

        An `Exception` gives its own traceback's last frame; a `CascadeError`
        (or any other value the driver returned) has no traceback, so only
        its message is available.
        """
        if isinstance(error, Exception):
            tb = traceback.extract_tb(error.__traceback__)
            frame_info = tb[-1] if tb else None
            file = os.path.basename(frame_info.filename) if frame_info else "?"
            line = (frame_info.lineno or 0) if frame_info else 0
            return f"{type(error).__name__}: {error}", file, line
        message = getattr(error, "message", None) or str(error)
        return message, "?", 0

    def execute(
        self,
        driver: CascadeCMSRestDriver | None = None,
        executor: Executor | None = None,
    ) -> Any:
        """Walk the chain synchronously and return its final result.

        Each node runs in order, receiving the previous node's result. The
        walk stops at the first `CascadeError` or callback exception, and
        that object is returned as-is (never re-raised, never wrapped).

        `submit_requests()` uses the concurrent `execute_async()` path
        instead; this is the single-chain entry point.

        Args:
            driver: Driver to execute against; defaults to the chain's own.
            executor: Optional executor for sync callbacks (e.g. a
                `ProcessPoolExecutor` for CPU-bound work).

        Returns:
            The last node's result, or the error that stopped the chain.
        """
        driver = driver if driver is not None else self._driver

        result: Any = None
        node = self._head
        step = 0
        last_node_was_operation = False

        while node is not None:
            step += 1
            node.input = result

            if node.node_type == "operation":
                # Bare op name appended before dispatch (open-ended/"in
                # progress" per the design) — if this node fails below, this
                # is already the failing step's own label render_error needs.
                self._line.append_step((node.operation_type or "operation").upper())
                try:
                    requests = self._node_requests(node, result)
                except Exception as exc:  # noqa: BLE001 - chain reports, never raises
                    self._start_line_if_needed()
                    return self._stopped(step, exc)
                self._start_line_if_needed()
                self._log_requests(requests)
                try:
                    raw = driver._submitRequests(requests)
                except Exception as exc:  # noqa: BLE001 - chain reports, never raises
                    return self._stopped(step, exc)
                value, stop = self._resolve_operation_result(node, raw)
                if stop:
                    return self._stopped(step, value)
                last_node_was_operation = True
            else:
                try:
                    value = self._run_callback_sync(node, result, driver, executor)
                except Exception as exc:  # noqa: BLE001 - chain reports, never raises
                    self._line.append_step(node.name)
                    return self._stopped(step, exc)
                self._line.append_step(f"{node.name}: {type(value).__name__}")
                last_node_was_operation = False

            node.output = value
            result = value
            node = node.next

        # Only an operation-terminated chain needs its return type spelled
        # out as its own trailing segment (the `EDIT -> CascadeSuccess`
        # asymmetry) — a callback-terminated chain already named its own
        # result type in its `fn_name: Type` segment above, so appending it
        # again here would render a duplicate trailing type.
        if last_node_was_operation:
            self._line.append_step(type(result).__name__)
        if self._logger:
            self._logger.flush_chain(self._line)
        return result

    async def execute_async(self, executor: Executor | None = None) -> Any:
        """Walk the chain on the event loop and return its final result.

        Same semantics as `execute()`, but requests are awaited on the
        driver's shared session so every chain in a batch runs concurrently
        (bounded by the driver's `MAX_REQUESTS` semaphore) while the nodes
        *within* each chain stay strictly sequential.

        Args:
            executor: Optional executor for sync callbacks.

        Returns:
            The last node's result, or the error that stopped the chain.
        """
        result: Any = None
        node = self._head
        step = 0
        last_node_was_operation = False

        while node is not None:
            step += 1
            node.input = result

            if node.node_type == "operation":
                self._line.append_step((node.operation_type or "operation").upper())
                try:
                    requests = self._node_requests(node, result)
                except Exception as exc:  # noqa: BLE001 - chain reports, never raises
                    self._start_line_if_needed()
                    return self._stopped(step, exc)
                self._start_line_if_needed()
                self._log_requests(requests)
                try:
                    raw = await self._driver.execute_requests(requests)
                except Exception as exc:  # noqa: BLE001 - chain reports, never raises
                    return self._stopped(step, exc)
                value, stop = self._resolve_operation_result(node, raw)
                if stop:
                    return self._stopped(step, value)
                last_node_was_operation = True
            else:
                try:
                    value = await self._run_callback_async(node, result, executor)
                except Exception as exc:  # noqa: BLE001 - chain reports, never raises
                    self._line.append_step(node.name)
                    return self._stopped(step, exc)
                self._line.append_step(f"{node.name}: {type(value).__name__}")
                last_node_was_operation = False

            node.output = value
            result = value
            node = node.next

        if last_node_was_operation:
            self._line.append_step(type(result).__name__)
        if self._logger:
            self._logger.flush_chain(self._line)
        return result

    def _stopped(self, step: int, error: Any) -> Any:
        """Flush the chain's line plus its `v`/`!ERROR:` block, once, and
        hand back `error` as the chain's result.

        `step` is 1-based and, by construction, always equals the number of
        segments already appended to `self._line` (every code path above
        appends exactly one label — resolved or not — before either
        continuing to the next node or calling this) — so `step - 1` is the
        failing segment's own index.
        """
        if self._logger:
            message, file, line = self._error_location(error)
            self._logger.flush_chain_error(self._line, step - 1, message, file, line)
        return error


@dataclass
class ChainGroup:
    """One chain per identifier, produced by fanning a list-of-identifiers
    call out into independent chains (Approach A — see the scope-locking
    comment above `OperationChain._identifier_operation`).

    Forwards `.then()` and every `OperationChain` operation method to each
    wrapped chain in turn and returns `self`, so a list call reads exactly
    like the single-identifier form:
        operations.read([id1, id2]).then(cb).edit(payload_fn)
    fans out into 2 independent chains at `read`, then applies `then`/`edit`
    to both.

    `.then()` is spelled out explicitly since it is the primary documented
    chaining method and benefits from being visible/typed here; every other
    `OperationChain` method (edit, delete, copy, move, publish, checkIn,
    checkOut, ...) is forwarded via `__getattr__` instead of one explicit
    passthrough per method, so this class never needs to change again when
    `OperationChain` grows a new operation.
    """

    chains: list[OperationChain]

    def __iter__(self):
        return iter(self.chains)

    def __len__(self) -> int:
        return len(self.chains)

    def then(
        self,
        callback_fn: Callable[[Any], Any] | list[Callable[[Any], Any]],
    ) -> "ChainGroup":
        """Append `callback_fn` (or each function in the list) to every chain."""
        for chain in self.chains:
            chain.then(callback_fn)
        return self

    def __getattr__(self, name: str) -> Callable[..., "ChainGroup"]:
        """Forward any other `OperationChain` method to every wrapped chain.

        Raises `AttributeError` naturally (via `getattr` below) if `name`
        isn't a real `OperationChain` method, same as attribute access would.
        """

        def call_on_every_chain(*args: Any, **kwargs: Any) -> "ChainGroup":
            for chain in self.chains:
                getattr(chain, name)(*args, **kwargs)
            return self

        return call_on_every_chain


@dataclass
class Operations:
    """
    Fluent builder for Cascade CMS REST API operations.

    Every method starts a **new** `OperationChain` and returns it, so further
    `.then()` / operation calls extend that chain rather than a shared queue.
    Chains are collected until `submit_requests()` runs them all concurrently
    and clears them.

    For CPU-bound callbacks (image optimization, etc.), pass a ProcessPoolExecutor
    to submit_requests() for true parallelism. ThreadPoolExecutor is the default
    and works fine for I/O-bound callbacks.

    Example:
        cascade.operations.read(identifier).then(filter_files).then(process_images)
        cascade.operations.read(identifier).then([filter_files, process_images])
        cascade.operations.read(identifier).then(rewrite).edit(build_payload)

    A list of identifiers fans out into one independent chain per identifier
    (Approach A) rather than one chain covering all of them, so a partial
    failure is reported per-asset instead of collapsing the whole batch:
        cascade.operations.read([id1, id2, id3]).then(cb).edit(payload_fn)
    returns a `ChainGroup` of 3 chains, each running read -> cb -> edit on
    exactly one asset; `submit_requests()` then returns 3 results.
    """

    _driver: CascadeCMSRestDriver
    _chains: list[OperationChain] = field(default_factory=list)
    _logger: OperationLogger | None = None

    def _new_chain(self) -> OperationChain:
        """Start a chain and register it for the next `submit_requests()`."""
        chain = OperationChain(
            self._driver,
            _logger=self._logger,
            _index=len(self._chains) + 1,
        )
        self._chains.append(chain)
        return chain

    def _new_chains_for(
        self,
        identifiers: list[Any],
        apply: Callable[[OperationChain, Any], OperationChain],
    ) -> ChainGroup:
        """Fan a list of identifiers out into one independent chain each.

        `apply(chain, identifier)` starts that chain with whatever operation
        the caller is building (`chain.read(identifier, parser)`, etc.) —
        kept as a callback so this one method serves every identifier-taking
        operation instead of duplicating the fan-out per method.
        """
        return ChainGroup([apply(self._new_chain(), identifier) for identifier in identifiers])

    def _reset_chains(self) -> None:
        """Drop every registered chain; called once a batch has been executed."""
        self._chains.clear()

    def read(
        self,
        identifiers: IdentifierType | Path | list[IdentifierType | Path],
        parser=parse_assets,
    ) -> OperationChain | ChainGroup:
        """Start a chain with GET `read/{type}/{id-or-path}` for one or more assets.

        A list of identifiers returns a `ChainGroup` of independent chains,
        one per identifier (Approach A) — see the class docstring.
        """
        if isinstance(identifiers, list):
            return self._new_chains_for(
                identifiers, lambda chain, ident: chain.read(ident, parser)
            )
        return self._new_chain().read(identifiers, parser)

    def delete(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: deleteParameters | None = None,
        parser=parse_success,
    ) -> OperationChain | ChainGroup:
        """Start a chain with POST `delete/{type}/{id-or-path}` for one or more assets."""
        if isinstance(identifier, list):
            return self._new_chains_for(
                identifier, lambda chain, ident: chain.delete(ident, payload, parser)
            )
        return self._new_chain().delete(identifier, payload, parser)

    def create(self, payload: list[NewAsset] | NewAsset, parser=None) -> OperationChain:
        """Start a chain with POST `create` for one or more new assets."""
        return self._new_chain().create(payload, parser)

    def edit(
        self,
        payload: Asset | list[Asset] | Callable[[Any], Asset | list[Asset]],
        parser=parse_success,
    ) -> OperationChain:
        """Start a chain with POST `edit` saving one or more modified assets."""
        return self._new_chain().edit(payload, parser)

    def copy(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: copyParameters,
        parser=parse_success,
    ) -> OperationChain | ChainGroup:
        """Start a chain with POST `copy/{type}/{id-or-path}` for one or more assets."""
        if isinstance(identifier, list):
            return self._new_chains_for(
                identifier, lambda chain, ident: chain.copy(ident, payload, parser)
            )
        return self._new_chain().copy(identifier, payload, parser)

    def move(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: moveParameters,
        parser=parse_success,
    ) -> OperationChain | ChainGroup:
        """Start a chain with POST `move/{type}/{id-or-path}` to move/rename assets."""
        if isinstance(identifier, list):
            return self._new_chains_for(
                identifier, lambda chain, ident: chain.move(ident, payload, parser)
            )
        return self._new_chain().move(identifier, payload, parser)

    def publish(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: None | publishInformation = None,
        parser=parse_success,
    ) -> OperationChain | ChainGroup:
        """Start a chain with POST `publish/{type}/{id-or-path}` for one or more assets."""
        if isinstance(identifier, list):
            return self._new_chains_for(
                identifier, lambda chain, ident: chain.publish(ident, payload, parser)
            )
        return self._new_chain().publish(identifier, payload, parser)

    def search(
        self,
        payload: SearchInformation,
        parser=parse_list_elements,
    ) -> OperationChain:
        """Start a chain with POST `search` for the given search criteria."""
        return self._new_chain().search(payload, parser)

    # -------Asset Controls-------
    def checkIn(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: Comment,
        parser=parse_success,
    ) -> OperationChain | ChainGroup:
        """Start a chain with POST `checkIn/{type}/{id-or-path}` for one or more assets."""
        if isinstance(identifier, list):
            return self._new_chains_for(
                identifier, lambda chain, ident: chain.checkIn(ident, payload, parser)
            )
        return self._new_chain().checkIn(identifier, payload, parser)

    def checkOut(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        parser=parse_checked_out_asset,
    ) -> OperationChain | ChainGroup:
        """Start a chain with POST `checkOut/{type}/{id-or-path}` for one or more assets."""
        if isinstance(identifier, list):
            return self._new_chains_for(
                identifier, lambda chain, ident: chain.checkOut(ident, parser)
            )
        return self._new_chain().checkOut(identifier, parser)

    def listSites(self, parser=parse_list_elements) -> OperationChain:
        """Start a chain with GET `listSites` to list all sites."""
        return self._new_chain().listSites(parser)

    def readAudits(
        self,
        payload: auditParameters,
        parser=parse_list_elements,
    ) -> OperationChain:
        """Start a chain with GET `readAudits` for entries matching the criteria."""
        return self._new_chain().readAudits(payload, parser)

    def listSubscribers(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        parser=parse_list_elements,
    ) -> OperationChain | ChainGroup:
        """Start a chain with GET `listSubscribers/{type}/{id-or-path}` for one or more assets."""
        if isinstance(identifier, list):
            return self._new_chains_for(
                identifier, lambda chain, ident: chain.listSubscribers(ident, parser)
            )
        return self._new_chain().listSubscribers(identifier, parser)

    def siteCopy(
        self,
        payload: SiteCopyParameter,
        parser=parse_success,
    ) -> OperationChain:
        """Start a chain with POST `siteCopy` to copy an entire site."""
        return self._new_chain().siteCopy(payload, parser)

    def readAccessRights(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        parser=parse_access_rights,
    ) -> OperationChain | ChainGroup:
        """Start a chain with GET `readAccessRights/{type}/{id-or-path}` for one or more assets."""
        if isinstance(identifier, list):
            return self._new_chains_for(
                identifier, lambda chain, ident: chain.readAccessRights(ident, parser)
            )
        return self._new_chain().readAccessRights(identifier, parser)

    def editAccessRights(
        self,
        payload: accessRightsInformationPayload,
    ) -> OperationChain:
        """Start a chain with POST `editAccessRights` to update an asset's ACL."""
        return self._new_chain().editAccessRights(payload)

    def readWorkflowSettings(
        self,
        identifier: IdentifierType | Path,
        parser=parse_workflow_settings,
    ) -> OperationChain:
        """Start a chain with GET `readWorkflowSettings/{type}/{id-or-path}`."""
        return self._new_chain().readWorkflowSettings(identifier, parser)

    def editWorkflowSettings(
        self,
        payload: workflowSettingsPayload,
        parser=parse_success,
    ) -> OperationChain:
        """Start a chain with POST `editWorkflowSettings/{type}/{id}` for an asset."""
        return self._new_chain().editWorkflowSettings(payload, parser)

    def listMessages(self, parser=parse_list_elements) -> OperationChain:
        """Start a chain with GET `listMessages` to list inbox messages."""
        return self._new_chain().listMessages(parser)

    def markMessage(self, message: Message) -> OperationChain:
        """Start a chain with POST `markMessage` to set a message's read state."""
        return self._new_chain().markMessage(message)

    def deleteMessage(self, message: Message) -> OperationChain:
        """Start a chain with POST `deleteMessage` to delete a message."""
        return self._new_chain().deleteMessage(message)

    def readPreferences(self, parser=parse_payloads) -> OperationChain:
        """Start a chain with GET `readPreferences` for the current user."""
        return self._new_chain().readPreferences(parser)

    def editPreference(self, payload: preference) -> OperationChain:
        """Start a chain with POST `editPreference` to update a user preference."""
        return self._new_chain().editPreference(payload)

    def readWorkflowInformation(
        self,
        identifier: IdentifierType | Path,
        parser=parse_workflow_information,
    ) -> OperationChain:
        """Start a chain with GET `readWorkflowInformation/{type}/{id-or-path}`."""
        return self._new_chain().readWorkflowInformation(identifier, parser)

    def performWorkflowTransition(
        self,
        identifier: IdentifierType | Path,
        payload: workflowTransitionInformation,
        parser=parse_success,
    ) -> OperationChain:
        """Start a chain with POST `performWorkflowTransition/...` to advance a workflow."""
        return self._new_chain().performWorkflowTransition(identifier, payload, parser)
