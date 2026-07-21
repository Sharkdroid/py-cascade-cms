from dataclasses import dataclass, field
from functools import partial
from typing import Callable, Any, Self
import asyncio
from concurrent.futures import Executor
from .driver import CascadeCMSRestDriver, RequestExecutor
from .cmstypes import (
    Asset,
    CascadeError,
    CheckedOutAsset,
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
    parse_checked_out_asset,
    parse_create_asset,
    parse_workflow_information,
    parse_workflow_settings,
    preference,
    publishInformation,
    resolve_identifier,
    workflowInformation,
    workflowSettingsPayload,
    workflowTransitionInformation,
    parse_payloads,
    set_checkedout,
    parse_assets,
    parse_list_elements,
)


@dataclass
class Operations:
    """
    Fluent builder for Cascade CMS REST API operations.
    
    Stores callbacks at the instance level to support multiple concurrent
    Operations chains without interference.
    
    For CPU-bound callbacks (image optimization, etc.), pass a ProcessPoolExecutor
    to submit_requests() for true parallelism. ThreadPoolExecutor is the default
    and works fine for I/O-bound callbacks.
    
    Example:
        cascade.operations.read(identifier).then(filter_files).then(process_images)
        cascade.operations.read(identifier).then([filter_files, process_images])
    """
    _driver: CascadeCMSRestDriver
    _callbacks: list[Callable[[Any], Any]] = field(default_factory=list)

    def then(self, callback_fn: Callable[[Any], Any] | list[Callable[[Any], Any]]) -> Self:
        """
        Register one or more callback functions to execute on results.
        
        Callbacks are invoked sequentially per result:
            result1 → callback1 → callback2
            result2 → callback1 → callback2
            ...
        
        Args:
            callback_fn: A single callback or list of callbacks.
                        Each receives one result object.
                        Both sync and async functions are supported.
        
        Returns:
            Self for method chaining.
        
        Example:
            def filter_files(result):
                if result.get('asset_type') == 'file':
                    print(f"Found file: {result.identifier}")
            
            cascade.operations.read(identifier).then(filter_files)
        """
        if isinstance(callback_fn, list):
            self._callbacks.extend(callback_fn)
        else:
            self._callbacks.append(callback_fn)
        return self

    async def _execute_callbacks_on_result(self, result: Any, executor: Executor | None = None) -> None:
        """
        Execute all registered callbacks sequentially on a single result.
        
        If a callback raises an exception, it's logged and execution continues
        to the next callback (not the next result).
        
        Args:
            result: A single result object from the API response.
            executor: Optional Executor for sync callbacks.
                     Use ThreadPoolExecutor (default) for I/O-bound work.
                     Use ProcessPoolExecutor for CPU-bound work (image optimization, etc.).
                     If None, uses default ThreadPoolExecutor.
        """
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    # Async callback: await it directly
                    await callback(result)
                else:
                    # Sync callback: run in executor to avoid blocking the event loop
                    # ProcessPoolExecutor: CPU-bound work (true parallelism, no GIL)
                    # ThreadPoolExecutor: I/O-bound work (default, lightweight)
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(executor, callback, result)
            except Exception as e:
                # Log and continue to next callback
                self._driver.warn(
                    f"Callback {callback.__name__} failed: {type(e).__name__}: {e}"
                )

    # ===== EXISTING READ/WRITE OPERATIONS (unchanged) =====
    # Keep all the existing methods below as they are.
    # The only changes are:
    # 1. Operations now stores callbacks in _callbacks (instance-level)
    # 2. _execute_callbacks_on_result() is called from wrapper._submitRequests()

    def read(
        self,
        identifiers: IdentifierType | Path | list[IdentifierType | Path],
        parser=parse_assets,
    ) -> Self:
        if not isinstance(identifiers, list):
            identifiers = [identifiers]
        for single_asset in identifiers:
            url = self._driver._build_url(
                self.read.__name__,
                *resolve_identifier(single_asset),
            )
            request = RequestExecutor[Asset](url, "GET", parser)
            self._driver.pending_requests.append(request)
        return self

    def delete(
        self,
        identifier: IdentifierType | Path,
        payload: deleteParameters | None = None,
        parser=None,
    ) -> Self:
        url = self._driver._build_url(
            self.delete.__name__,
            *resolve_identifier(identifier),
        )
        request = RequestExecutor[Asset](url, "POST", payload=payload)
        self._driver.pending_requests.append(request)
        return self

    def create(self, payload: list[NewAsset] | NewAsset, parser=None) -> Self:
        if not isinstance(payload, list):
            payload = [payload]
        for single_asset in payload:
            url = self._driver._build_url(self.create.__name__)
            # Bind asset_type NOW while we have it
            bound_parser = partial(parse_create_asset, pass_type=single_asset.asset_type)
            request = RequestExecutor[IdentifierType](
                url, 
                "POST", 
                payload=single_asset, 
                parser=bound_parser  # This now only expects (raw)
            )
            self._driver.pending_requests.append(request)
        return self

    def edit(self, payload: list[Asset] | Asset, parser=None) -> Self:
        if not isinstance(payload, list):
            payload = [payload]
        for single_asset in payload:
            url = self._driver._build_url(self.edit.__name__)
            request = RequestExecutor[Asset](url, "POST", payload=single_asset)
            self._driver.pending_requests.append(request)
        return self

    def copy(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: copyParameters,
        parser=None,
    ) -> Self:
        if not isinstance(identifier, list):
            identifier = [identifier]
        for single_identifier in identifier:
            url = self._driver._build_url(
                self.copy.__name__,
                *resolve_identifier(single_identifier),
            )
            request = RequestExecutor[SimplePayload](url, "POST", payload=payload)
            self._driver.pending_requests.append(request)
        return self

    def move(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: moveParameters,
        parser=None,
    ) -> Self:
        if not isinstance(identifier, list):
            identifier = [identifier]
        for single_identifier in identifier:
            url = self._driver._build_url(
                self.move.__name__,
                *resolve_identifier(single_identifier),
            )
            request = RequestExecutor[SimplePayload](url, "POST", payload=payload)
            self._driver.pending_requests.append(request)
        return self

    def publish(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: None | publishInformation = None,
        parser=None,
    ) -> Self:
        if not isinstance(identifier, list):
            identifier = [identifier]
        for single_identifier in identifier:
            url = self._driver._build_url(
                self.publish.__name__,
                *resolve_identifier(single_identifier),
            )
            request = RequestExecutor[CascadeError](url, "POST", payload=payload)
            self._driver.pending_requests.append(request)
        return self

    def search(
        self,
        payload: SearchInformation,
        parser=parse_list_elements,
    ) -> Self:
        assert isinstance(payload, SearchInformation)
        url = self._driver._build_url(self.search.__name__)
        request = RequestExecutor[ListElements](url, "POST", parser, payload)
        self._driver.pending_requests.append(request)
        return self

    # -------Asset Controls-------
    def checkIn(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        payload: Comment,
        parser=None,
    ) -> Self:
        if not isinstance(identifier, list):
            identifier = [identifier]
        for single_asset in identifier:
            segments = resolve_identifier(single_asset)
            set_checkedout("/".join(segments))
            url = self._driver._build_url(self.checkIn.__name__, *segments)
            request = RequestExecutor[CascadeError](url, "POST", payload=payload)
            self._driver.pending_requests.append(request)
        return self

    def checkOut(
        self,
        identifier: IdentifierType | Path | list[IdentifierType | Path],
        parser=parse_checked_out_asset,
    ) -> Self:
        if not isinstance(identifier, list):
            identifier = [identifier]
        for single_asset in identifier:
            segments = resolve_identifier(single_asset)
            set_checkedout("/".join(segments))
            url = self._driver._build_url(self.checkOut.__name__, *segments)
            request = RequestExecutor[CheckedOutAsset](url, "POST", parser)
            self._driver.pending_requests.append(request)
        return self

    def listSites(self, parser=parse_list_elements) -> Self:
        url = self._driver._build_url(self.listSites.__name__)
        request = RequestExecutor[ListElements](url, "GET", parser)
        self._driver.pending_requests.append(request)
        return self

    def readAudits(
        self,
        payload: auditParameters,
        parser=parse_list_elements,
    ) -> Self:
        url = self._driver._build_url(self.readAudits.__name__)
        request = RequestExecutor[ListElements](url, "GET", parser, payload)
        self._driver.pending_requests.append(request)
        return self

    def listSubscribers(
        self,
        identifier: IdentifierType | Path,
        parser=parse_list_elements,
    ) -> None:
        url = self._driver._build_url(
            self.listSubscribers.__name__,
            *resolve_identifier(identifier),
        )
        request = RequestExecutor[ListElements](
            url,
            "GET",
            parser=parser,
        )
        self._driver.pending_requests.append(request)
        return

    def siteCopy(
        self,
        payload: SiteCopyParameter,
        parser=None,
    ) -> Self:
        url = self._driver._build_url(self.siteCopy.__name__)
        request = RequestExecutor[CascadeError](url, "POST", payload=payload)
        self._driver.pending_requests.append(request)
        return self

    def readAccessRights(
        self,
        identifier: IdentifierType | Path,
        parser=parse_access_rights,
    ) -> Self:
        url = self._driver._build_url(
            self.readAccessRights.__name__,
            *resolve_identifier(identifier),
        )
        request = RequestExecutor[accessRightsInformationPayload](url, "GET", parser)
        self._driver.pending_requests.append(request)
        return self

    def editAccessRights(
        self,
        payload: accessRightsInformationPayload,
    ) -> Self:
        url = self._driver._build_url(self.editAccessRights.__name__)
        request = RequestExecutor[CascadeError](url, "POST", payload=payload)
        self._driver.pending_requests.append(request)
        return self

    def readWorkflowSettings(
        self,
        identifier: IdentifierType | Path,
        parser=parse_workflow_settings,
    ) -> Self:
        url = self._driver._build_url(
            self.readWorkflowSettings.__name__,
            *resolve_identifier(identifier),
        )
        request = RequestExecutor[workflowSettingsPayload](url, "GET", parser)
        self._driver.pending_requests.append(request)
        return self

    def editWorkflowSettings(
        self,
        payload: workflowSettingsPayload,
        parser=None,
    ) -> None:
        id_fields = payload.body["identifier"]
        url = self._driver._build_url(
            self.editWorkflowSettings.__name__,
            id_fields.get_type,
            id_fields.get_id,
        )
        request = RequestExecutor[CascadeError](url, "POST", payload=payload)
        self._driver.pending_requests.append(request)

    def listMessages(self, parser=parse_list_elements) -> Self:
        url = self._driver._build_url(self.listMessages.__name__)
        request = RequestExecutor[ListElements](url, "GET", parser)
        self._driver.pending_requests.append(request)
        return self

    def markMessage(self, message: Message) -> None:
        url = self._driver._build_url(
            self.markMessage.__name__, message.__class__.__name__, message.m_id
        )
        request = RequestExecutor[CascadeError](url, "POST", payload=message)
        self._driver.pending_requests.append(request)

    def deleteMessage(self, message: Message) -> None:
        url = self._driver._build_url(
            self.deleteMessage.__name__, message.__class__.__name__, message.m_id
        )
        request = RequestExecutor[CascadeError](url, "POST")
        self._driver.pending_requests.append(request)

    def readPreferences(self, parser=parse_payloads) -> Self:
        url = self._driver._build_url(self.readPreferences.__name__)
        request = RequestExecutor[SimplePayload](url, "GET", parser)
        self._driver.pending_requests.append(request)
        return self

    def editPreference(self, payload: preference) -> None:
        url = self._driver._build_url(self.editPreference.__name__)
        request = RequestExecutor[CascadeError](url, "POST", payload=payload)
        self._driver.pending_requests.append(request)

    def readWorkflowInformation(
        self,
        identifier: IdentifierType | Path,
        parser=parse_workflow_information,
    ) -> Self:
        url = self._driver._build_url(
            self.readWorkflowInformation.__name__,
            *resolve_identifier(identifier),
        )
        request = RequestExecutor[workflowInformation](url, "GET", parser)
        self._driver.pending_requests.append(request)
        return self

    def performWorkflowTransition(
        self,
        identifier: IdentifierType | Path,
        payload: workflowTransitionInformation,
        parser=None,
    ) -> Self:
        url = self._driver._build_url(
            self.performWorkflowTransition.__name__,
            *resolve_identifier(identifier),
        )
        request = RequestExecutor[CascadeError](url, "POST", payload=payload)
        self._driver.pending_requests.append(request)
        return self
