import asyncio
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal, Self

from .cmstypes import (
    Asset,
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
    set_checkedout,
    workflowSettingsPayload,
    workflowTransitionInformation,
)
from .driver import CascadeCMSRestDriver, RequestExecutor
from .operation_logger import OperationLogger

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
        cascade.operations.read(identifier).then(rewrite).edit(identifier, build_payload)
    """

    _driver: CascadeCMSRestDriver
    _head: Node | None = None
    _current: Node | None = None
    _logger: OperationLogger | None = None
    _asset_identifier: IdentifierType | Path | None = None
    _index: int = 0

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

    def _log_operation(
        self,
        name: str,
        url: str,
        payload: Any,
        parser: Any,
        identifier: Any,
    ) -> None:
        if self._logger:
            with self._logger.operation_scope(name):
                self._logger.log_operation(name, url, payload, parser, identifier)

    def _identifier_operation(
        self,
        *,
        op_name: str,
        log_name: str,
        method: HTTPMethod,
        identifiers: Any,
        parser: Any,
        payload: Any = None,
        log_payload: Any = None,
        checkout_ledger: bool = False,
    ) -> Self:
        """Append a node for an endpoint addressed as `<op>/{type}/{id-or-path}`.

        Handles both the single-identifier and list-of-identifiers forms; a
        list produces one request per identifier inside a single node, and
        that node's result is a list.
        """
        multi = isinstance(identifiers, list)
        items = identifiers if multi else [identifiers]

        requests: list[RequestExecutor] = []
        for item in items:
            segments = resolve_identifier(item)
            if checkout_ledger:
                set_checkedout("/".join(segments))
            url = self._driver._build_url(op_name, *segments)
            requests.append(
                RequestExecutor(url, method, parser, payload=payload, identifier=item)
            )
            self._log_operation(
                log_name,
                url,
                log_payload if log_payload is not None else payload,
                parser,
                item,
            )

        return self._add_operation(
            op_name,
            requests,
            identifier=identifiers,
            payload=payload,
            parser=parser,
            multi=multi,
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
        identifiers: IdentifierType | Path | list[IdentifierType | Path],
        parser=parse_assets,
    ) -> Self:
        """Append a GET `read/{type}/{id-or-path}` step for one or more assets."""
        return self._identifier_operation(
            op_name="read",
            log_name="READ",
            method="GET",
            identifiers=identifiers,
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
            log_name="DELETE",
            method="POST",
            identifiers=identifier,
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
            self._log_operation("CREATE", url, single_asset, bound_parser, None)

        return self._add_operation(
            "create", requests, payload=payload, parser=parser, multi=multi
        )

    def edit(
        self,
        identifier: IdentifierType | Path | None,
        payload: Asset | list[Asset] | Callable[[Any], Asset | list[Asset]],
        parser=parse_success,
    ) -> Self:
        """Append a POST `edit` step saving one or more modified assets.

        The `edit` endpoint is addressed by the payload rather than the URL;
        `identifier` is what the chain reports this step against in logs and
        errors.

        `payload` may be a callable, in which case it is invoked with the
        previous node's result when the chain runs — that is how
        `read → then(transform) → edit` writes back the transformed asset.

        Args:
            identifier: Asset this edit targets, used for error reporting.
            payload: The asset(s) to save, or a callable producing them from
                the previous node's result.
            parser: Response parser, defaults to `parse_success`.
        """
        if callable(payload):
            builder = partial(self._build_edit_requests, parser=parser, resolver=payload)
            return self._add_operation(
                "edit",
                None,
                identifier=identifier,
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
            identifier=identifier,
            payload=payload,
            parser=parser,
            multi=multi,
        )

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

        requests: list[RequestExecutor] = []
        for single_asset in assets:
            url = self._driver._build_url("edit")
            identifier = single_asset.get("path")
            requests.append(
                RequestExecutor(
                    url, "POST", parser, payload=single_asset, identifier=identifier
                )
            )
            self._log_operation("EDIT", url, single_asset, parser, identifier)
        return requests

    def copy(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: copyParameters,
        parser=parse_success,
    ) -> Self:
        """Append a POST `copy/{type}/{id-or-path}` step for one or more assets."""
        return self._identifier_operation(
            op_name="copy",
            log_name="COPY",
            method="POST",
            identifiers=identifier,
            payload=payload,
            parser=parser,
        )

    def move(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: moveParameters,
        parser=parse_success,
    ) -> Self:
        """Append a POST `move/{type}/{id-or-path}` step to move/rename assets."""
        return self._identifier_operation(
            op_name="move",
            log_name="MOVE",
            method="POST",
            identifiers=identifier,
            payload=payload,
            parser=parser,
        )

    def publish(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: None | publishInformation = None,
        parser=parse_success,
    ) -> Self:
        """Append a POST `publish/{type}/{id-or-path}` step for one or more assets."""
        return self._identifier_operation(
            op_name="publish",
            log_name="PUBLISH",
            method="POST",
            identifiers=identifier,
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
        self._log_operation("SEARCH", url, payload, parser, None)
        return self._add_operation("search", [request], payload=payload, parser=parser)

    # -------Asset Controls-------
    def checkIn(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: Comment,
        parser=parse_success,
    ) -> Self:
        """Append a POST `checkIn/...` step, toggling the local checkout ledger."""
        return self._identifier_operation(
            op_name="checkIn",
            log_name="CHECKIN",
            method="POST",
            identifiers=identifier,
            payload=payload,
            parser=parser,
            checkout_ledger=True,
        )

    def checkOut(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        parser=parse_checked_out_asset,
    ) -> Self:
        """Append a POST `checkOut/...` step, toggling the local checkout ledger."""
        return self._identifier_operation(
            op_name="checkOut",
            log_name="CHECKOUT",
            method="POST",
            identifiers=identifier,
            parser=parser,
            checkout_ledger=True,
        )

    def listSites(self, parser=parse_list_elements) -> Self:
        """Append a GET `listSites` step listing all sites."""
        url = self._driver._build_url("listSites")
        request = RequestExecutor[ListElements](url, "GET", parser)
        self._log_operation("LISTSITES", url, None, parser, None)
        return self._add_operation("listSites", [request], parser=parser)

    def readAudits(
        self,
        payload: auditParameters,
        parser=parse_list_elements,
    ) -> Self:
        """Append a GET `readAudits` step for audit entries matching the criteria."""
        url = self._driver._build_url("readAudits")
        request = RequestExecutor[ListElements](url, "GET", parser, payload=payload)
        self._log_operation("READAUDITS", url, payload, parser, payload.by_identifier)
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
            log_name="LISTSUBSCRIBERS",
            method="GET",
            identifiers=identifier,
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
        self._log_operation("SITECOPY", url, payload, parser, None)
        return self._add_operation("siteCopy", [request], payload=payload, parser=parser)

    def readAccessRights(
        self,
        identifier: IdentifierType | Path,
        parser=parse_access_rights,
    ) -> Self:
        """Append a GET `readAccessRights/{type}/{id-or-path}` step for an asset."""
        return self._identifier_operation(
            op_name="readAccessRights",
            log_name="READACCESSRIGHTS",
            method="GET",
            identifiers=identifier,
            parser=parser,
        )

    def editAccessRights(
        self,
        payload: accessRightsInformationPayload,
    ) -> Self:
        """Append a POST `editAccessRights` step updating an asset's ACL."""
        url = self._driver._build_url("editAccessRights")
        request = RequestExecutor(url, "POST", parse_success, payload=payload)
        self._log_operation("EDITACCESSRIGHTS", url, payload, None, None)
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
            log_name="READWORKFLOWSETTINGS",
            method="GET",
            identifiers=identifier,
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
        self._log_operation("EDITWORKFLOWSETTINGS", url, payload, parser, id_fields)
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
        self._log_operation("LISTMESSAGES", url, None, parser, None)
        return self._add_operation("listMessages", [request], parser=parser)

    def markMessage(self, message: Message) -> Self:
        """Append a POST `markMessage` step setting a message's read state."""
        url = self._driver._build_url(
            "markMessage", message.__class__.__name__, message.m_id
        )
        request = RequestExecutor(url, "POST", parse_success, payload=message)
        self._log_operation("MARKMESSAGE", url, message, None, None)
        return self._add_operation(
            "markMessage", [request], payload=message, parser=parse_success
        )

    def deleteMessage(self, message: Message) -> Self:
        """Append a POST `deleteMessage` step deleting a message."""
        url = self._driver._build_url(
            "deleteMessage", message.__class__.__name__, message.m_id
        )
        request = RequestExecutor(url, "POST", parse_success)
        self._log_operation("DELETEMESSAGE", url, None, None, None)
        return self._add_operation("deleteMessage", [request], parser=parse_success)

    def readPreferences(self, parser=parse_payloads) -> Self:
        """Append a GET `readPreferences` step for the current user."""
        url = self._driver._build_url("readPreferences")
        request = RequestExecutor[SimplePayload](url, "GET", parser)
        self._log_operation("READPREFERENCES", url, None, parser, None)
        return self._add_operation("readPreferences", [request], parser=parser)

    def editPreference(self, payload: preference) -> Self:
        """Append a POST `editPreference` step updating a user preference."""
        url = self._driver._build_url("editPreference")
        request = RequestExecutor(url, "POST", parse_success, payload=payload)
        self._log_operation("EDITPREFERENCE", url, payload, None, None)
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
            log_name="READWORKFLOWINFORMATION",
            method="GET",
            identifiers=identifier,
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
            log_name="PERFORMWORKFLOWTRANSITION",
            method="POST",
            identifiers=identifier,
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

    def _log_start(self) -> None:
        if self._logger:
            self._logger.log_chain_start(self._index, self._asset_identifier)

    def _log_node(self, step: int, node: Node) -> None:
        if self._logger:
            self._logger.log_node_execution(
                step,
                node.node_type,
                node.operation_type,
                node.name if node.node_type == "callback" else None,
                chain_index=self._index,
            )

    def _log_stop(self, step: int, node: Node, error: Any) -> None:
        if self._logger:
            self._logger.log_chain_error(
                self._index,
                self._asset_identifier,
                step,
                node.node_type,
                node.operation_type or node.name,
                error,
            )

    def _log_finish(self, result: Any) -> None:
        if self._logger:
            self._logger.log_chain_complete(self._index, self._asset_identifier, result)

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
        self._log_start()

        result: Any = None
        node = self._head
        step = 0

        while node is not None:
            step += 1
            self._log_node(step, node)

            if node.node_type == "operation":
                try:
                    requests = self._node_requests(node, result)
                    raw = driver._submitRequests(requests)
                except Exception as exc:  # noqa: BLE001 - chain reports, never raises
                    return self._stopped(step, node, exc)
                value, stop = self._resolve_operation_result(node, raw)
                if stop:
                    return self._stopped(step, node, value)
            else:
                try:
                    value = self._run_callback_sync(node, result, driver, executor)
                except Exception as exc:  # noqa: BLE001 - chain reports, never raises
                    return self._stopped(step, node, exc)

            result = value
            node = node.next

        self._log_finish(result)
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
        self._log_start()

        result: Any = None
        node = self._head
        step = 0

        while node is not None:
            step += 1
            self._log_node(step, node)

            if node.node_type == "operation":
                try:
                    requests = self._node_requests(node, result)
                    raw = await self._driver.execute_requests(requests)
                except Exception as exc:  # noqa: BLE001 - chain reports, never raises
                    return self._stopped(step, node, exc, progress=True)
                value, stop = self._resolve_operation_result(node, raw)
                if stop:
                    return self._stopped(step, node, value, progress=True)
            else:
                try:
                    value = await self._run_callback_async(node, result, executor)
                except Exception as exc:  # noqa: BLE001 - chain reports, never raises
                    return self._stopped(step, node, exc, progress=True)

            result = value
            node = node.next

        self._log_finish(result)
        if self._logger:
            self._logger.log_progress(failed=False)
        return result

    def _stopped(
        self,
        step: int,
        node: Node,
        error: Any,
        progress: bool = False,
    ) -> Any:
        """Log the stopping node and hand the error back as the chain result."""
        if isinstance(error, Exception) and not isinstance(error, CascadeError) and self._logger:
            self._logger.log_python_error(error)
        self._log_stop(step, node, error)
        if progress and self._logger:
            self._logger.log_progress(failed=True)
        return error


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
        cascade.operations.read(identifier).then(rewrite).edit(identifier, build_payload)
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

    def _reset_chains(self) -> None:
        """Drop every registered chain; called once a batch has been executed."""
        self._chains.clear()

    def read(
        self,
        identifiers: IdentifierType | Path | list[IdentifierType | Path],
        parser=parse_assets,
    ) -> OperationChain:
        """Start a chain with GET `read/{type}/{id-or-path}` for one or more assets."""
        return self._new_chain().read(identifiers, parser)

    def delete(
        self,
        identifier: IdentifierType | Path,
        payload: deleteParameters | None = None,
        parser=parse_success,
    ) -> OperationChain:
        """Start a chain with POST `delete/{type}/{id-or-path}` for an asset."""
        return self._new_chain().delete(identifier, payload, parser)

    def create(self, payload: list[NewAsset] | NewAsset, parser=None) -> OperationChain:
        """Start a chain with POST `create` for one or more new assets."""
        return self._new_chain().create(payload, parser)

    def edit(
        self,
        identifier: IdentifierType | Path | None,
        payload: Asset | list[Asset] | Callable[[Any], Asset | list[Asset]],
        parser=parse_success,
    ) -> OperationChain:
        """Start a chain with POST `edit` saving one or more modified assets."""
        return self._new_chain().edit(identifier, payload, parser)

    def copy(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: copyParameters,
        parser=parse_success,
    ) -> OperationChain:
        """Start a chain with POST `copy/{type}/{id-or-path}` for one or more assets."""
        return self._new_chain().copy(identifier, payload, parser)

    def move(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: moveParameters,
        parser=parse_success,
    ) -> OperationChain:
        """Start a chain with POST `move/{type}/{id-or-path}` to move/rename assets."""
        return self._new_chain().move(identifier, payload, parser)

    def publish(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: None | publishInformation = None,
        parser=parse_success,
    ) -> OperationChain:
        """Start a chain with POST `publish/{type}/{id-or-path}` for one or more assets."""
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
    ) -> OperationChain:
        """Start a chain with POST `checkIn/{type}/{id-or-path}` for one or more assets."""
        return self._new_chain().checkIn(identifier, payload, parser)

    def checkOut(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        parser=parse_checked_out_asset,
    ) -> OperationChain:
        """Start a chain with POST `checkOut/{type}/{id-or-path}` for one or more assets."""
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
        identifier: IdentifierType | Path,
        parser=parse_list_elements,
    ) -> OperationChain:
        """Start a chain with GET `listSubscribers/{type}/{id-or-path}` for an asset."""
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
        identifier: IdentifierType | Path,
        parser=parse_access_rights,
    ) -> OperationChain:
        """Start a chain with GET `readAccessRights/{type}/{id-or-path}` for an asset."""
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
