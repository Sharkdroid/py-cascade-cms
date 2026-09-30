import json
import uuid
import warnings
from collections.abc import Callable
from datetime import datetime
from typing import (
    Annotated,
    Any,
    ClassVar,
    Literal,
    NamedTuple,
    NoReturn,
    Self,
    TypeVar,
    cast,
)

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    ValidationError,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.dataclasses import dataclass as pydantic_dataclass

T = TypeVar("T")

# ----- TYPE ALIASES & HELPERS -----

type IdentityTypes = Literal[
    "group",
    "user",
    "role",
]


type AssetTypes = Literal[
    # Asset Factories
    "assetfactory",
    "assetfactorycontainer",
    # Blocks
    "block",
    "block_FEED",
    "block_INDEX",
    "block_TEXT",
    "block_XHTML_DATADEFINITION",
    "block_XML",
    "block_TWITTER_FEED",
    # Connectors
    "connectorcontainer",
    "facebookconnector",
    "googleanalyticsconnector",
    "twitterconnector",
    "wordpressconnector",
    # Content Types
    "contenttype",
    "contenttypecontainer",
    # Data Definitions
    "datadefinition",
    "datadefinitioncontainer",
    "structureddatadefinition",
    "structureddatadefinitioncontainer",
    # Destinations
    "destination",
    "sitedestinationcontainer",
    # Editor
    "editorconfiguration",
    # Files & Folders
    "file",
    "folder",
    # Formats
    "format",
    "format_SCRIPT",
    "format_XSLT",
    # Metadata Sets
    "metadataset",
    "metadatasetcontainer",
    # Page Configurations
    "page",
    "pageconfiguration",
    "pageconfigurationset",
    "pageconfigurationsetcontainer",
    "pageregion",
    # Publish Sets
    "publishset",
    "publishsetcontainer",
    # Misc site content
    "reference",
    "site",
    "symlink",
    "target",
    "template",
    # Shared Fields
    "sharedfield",
    "sharedfieldcontainer",
    # Transports
    "transport",
    "transport_cloud",
    "transport_db",
    "transport_fs",
    "transport_ftp",
    "transportcontainer",
    # Users / Groups / Roles (admin)
    "message",
    # Workflows
    "workflow",
    "workflowdefinition",
    "workflowdefinitioncontainer",
    "workflowemail",
    "workflowemailcontainer",
    IdentityTypes,
]


type FieldsSearchTypes = Literal[
    # Basic fields
    "name",
    "path",
    "createdBy",
    "modifiedBy",
    # Metadata fields
    "author",
    "description",
    "displayName",
    "keywords",
    "summary",
    "teaser",
    "title",
    # Content fields
    "blob",  # binary file content
    "link",  # symlink link text
    "velocityFormatContent",  # Velocity/script format content
    "xml",  # WYSIWYG, data definition pages, text/XML blocks, templates, XSLT formats
]


type AuditTypes = Literal[
    "login",
    "login_failed",
    "logout",
    "start_workflow",
    "advance_workflow",
    "edit",
    "startedit",
    "copy",
    "create",
    "reference",
    "delete",
    "delete_unpublish",
    "check_in",
    "check_out",
    "activate_version",
    "publish",
    "unpublish",
    "recycle",
    "restore",
    "move",
]

# In-process ledger of asset "type/id" segments currently checked out via
# this library's checkIn/checkOut operations. This is purely local
# bookkeeping (toggled by set_checkedout) and is NOT synchronized with
# Cascade's actual server-side lock state, so it cannot detect an asset
# already checked out through another session or the Cascade UI.
ALL_CHECKOUT_ASSETS: set[str] = set()


# ----- UTILITY FUNCTIONS -----


def reformat_name(class_name: str):
    """Lowercase the first letter of a class name (e.g. "NewAsset" -> "newAsset")."""
    if class_name[0].isupper():
        return class_name[0].lower() + class_name[1:]
    return class_name


def set_checkedout(key: str):
    """Toggle a checkout-segment key in the local checkout ledger.

    Called once per checkIn/checkOut operation queued, so calling it twice
    for the same key (once on checkOut, once on the matching checkIn) flips
    it back out again rather than accumulating duplicates.
    """
    if key in ALL_CHECKOUT_ASSETS:
        ALL_CHECKOUT_ASSETS.discard(key)
    else:
        ALL_CHECKOUT_ASSETS.add(key)


# ----- PATH TYPES -----


class PathBase(BaseModel):
    """
    Base class for Path object

    Attributes:
        path (str): The path string
        site_id (uuid): unique identifier of the site associated with it (optional)
        site_name (str): name of the site associated with it (optional)
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    path: str
    site_id: uuid.UUID | None = Field(
        default=None, validation_alias="siteId", serialization_alias="siteId"
    )
    site_name: str | None = Field(
        default=None, validation_alias="siteName", serialization_alias="siteName"
    )

    # Cascade rejects dashed UUIDs for identifiers - serialize as bare hex.
    @field_serializer("site_id")
    def serialize_site_id(self, value: uuid.UUID | None) -> str | None:
        return value.hex if value is not None else None


class Path(PathBase):
    """
    Represents a Path object that can be called directly.
    Substitution for TypeIdentifers with asset id
    Attributes:
        asset_type (AssetTypes): (Required) the type of the asset
    """

    asset_type: AssetTypes

    # Duck-typed surface shared with IdentifierType (logging reads these).
    @property
    def get_id(self) -> None:
        return None

    @property
    def get_type(self) -> str:
        return str(self.asset_type)

    @property
    def get_path(self) -> str:
        return self.path


# ===== PAYLOAD MODELS (Request Data) =====

# ----- Payload Base & Core Models -----


class SimplePayload(BaseModel):
    """
    Base class for Cascade CMS Payloads:
    Payloads are containers for specific
    Cascade operations that accept inputs
    """

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
        validate_assignment=True,
        serialize_by_alias=True,
    )

    @model_serializer  # or @classmethod
    def format_builder(self) -> dict:
        """Wraps proper headers around payload data.

        Args:
            handler (SerializerFunctionWrapHandler): Pydantic validation function

        Returns:
            dict[str,object]: serialized wrapped inSerializerFunctionWrapHandler the inherit class name

            ```python
            {
                "searchInformation"{
                    ...
                }
            }
            ```

        """
        subclass_name = self.__class__.__name__
        fields_info = self.__class__.model_fields

        def dump(value: Any) -> Any:
            # This dict comprehension only aliases top-level keys; a nested
            # BaseModel passed through as-is would serialize under its
            # Python field names instead of its aliases, so recurse.
            if isinstance(value, BaseModel):
                return value.model_dump(by_alias=True)
            if isinstance(value, list):
                return [dump(item) for item in value]
            return value

        aliased = {
            (fields_info[name].serialization_alias or fields_info[name].alias or name): dump(value)
            for name, value in self.__dict__.items()
            if name in fields_info
        }
        return {reformat_name(subclass_name): aliased}


class NewAsset(SimplePayload):
    """Payload for the `create` operation.

    Requires exactly one of `site_name`/`site_id` and exactly one of
    `parent_folder_path`/`parent_folder_id` (enforced by
    `_check_required_alternatives`). Extra fields are allowed and passed
    through, since asset-type-specific properties vary per `asset_type`.
    """

    model_config = ConfigDict(
        extra="allow",
        validate_by_name=True,
        validate_by_alias=True,
    )

    name: str
    asset_type: AssetTypes
    site_name: str | None = Field(default=None, validation_alias="siteName", serialization_alias="siteName")
    site_id: uuid.UUID | None = Field(default=None, validation_alias="siteId", serialization_alias="siteId")
    parent_folder_path: str | None = Field(default=None, validation_alias="parentFolderPath", serialization_alias="parentFolderPath")
    parent_folder_id: uuid.UUID | None = Field(default=None, validation_alias="parentFolderId", serialization_alias="parentFolderId")

    @field_serializer("site_id", "parent_folder_id")
    def serialize_uuid_as_hex(self, value: uuid.UUID | None) -> str | None:
        return value.hex if value is not None else None

    @model_validator(mode="after")
    def _check_required_alternatives(self) -> Self:
        if (self.site_name is None) == (self.site_id is None):
            raise ValueError("Provide exactly one of site_name or site_id")
        if (self.parent_folder_path is None) == (self.parent_folder_id is None):
            raise ValueError(
                "Provide exactly one of parent_folder_path or parent_folder_id"
            )
        return self

    @model_serializer(mode="wrap")
    def serialize_as_asset(self, handler: SerializerFunctionWrapHandler) -> dict:
        """Cascade expects {"asset": {"<type>": {...}}}"""
        payload_dict = handler(self)
        asset_type = payload_dict.pop("asset_type")
        cleaned = {k: v for k, v in payload_dict.items() if v is not None}
        return {"asset": {asset_type: cleaned}}

    def dump_json(self) -> bytes:
        return new_asset_adapter.dump_json(self, by_alias=True)


# ===== RESPONSE MODELS (Received Data) =====

# ----- Core Response Models -----


class IdentifierType(BaseModel):
    """Resolved reference to a Cascade asset: its UUID, type, and optional path info.

    This is the "id-based" counterpart to `Path` (which references an
    asset by site + path string instead); see `resolve_identifier`.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
    )

    identifier: Annotated[uuid.UUID, Field(validation_alias='id', serialization_alias='id')]
    asset_type: Annotated[AssetTypes, Field(default=..., validation_alias="type", serialization_alias="type")]
    recycled: Annotated[bool | None, Field(default=None)] = None
    path: Annotated[PathBase | None, Field(default=None)] = None

    # Cascade rejects dashed UUIDs for identifiers - serialize as bare hex.
    @field_serializer("identifier")
    def serialize_identifier(self, value: uuid.UUID) -> str:
        return value.hex

    # getters
    @property
    def get_path(self):
        if self.path is not None:
            return self.path.path

    @property
    def get_sitename(self):
        if self.path is not None:
            return self.path.site_name

    @property
    def get_site_id(self):
        # site_id is optional and defaults to None when the response omits it.
        if self.path is not None:
            return self.path.site_id

    @property
    def get_id(self):
        return self.identifier.hex

    @property
    def get_type(self):
        return self.asset_type

    @model_validator(mode="before")
    @classmethod
    def reject_extra_fields(cls, values):
        if isinstance(values, dict):
            if "asset_type" in values and "type" not in values:
                values = {**values, "type": values["asset_type"]}
                values.pop("asset_type")
            if "identifier" in values and "id" not in values:
                values = {**values, "id": values["identifier"]}
                values.pop("identifier")

            allowed = {"id", "type", "recycled", "path"}
            extra = set(values) - allowed
            if extra:
                raise ValueError(
                    f"Identifier payload contains unexpected fields: {sorted(extra)}"
                )
        return values


def resolve_identifier(identifier: "IdentifierType | Path") -> tuple[str, ...]:
    """Returns the URL path segments (after the operation name) identifying this asset.

    IdentifierType resolves to (asset_type, id). Path resolves to
    (asset_type, siteName, path), matching the REST endpoint shape
    `.../{operation_name}/{asset_type}/{siteName}/{path}`.
    """
    if isinstance(identifier, IdentifierType):
        return (str(identifier.get_type), str(identifier.get_id))
    if identifier.site_name is None:
        raise ValueError("Path identifiers require site_name to build the request URL")
    return (str(identifier.asset_type), identifier.site_name, identifier.path)


class AssetLogIdentifier(NamedTuple):
    """Unvalidated (id, raw_type) pair used only to label an `edit`
    request/chain for logging.

    Not an `IdentifierType` substitute for URL-building: `edit()`'s request
    has no type/id URL segment (see `Chain._build_edit_requests`), so this
    only needs to satisfy the duck-typed `get_id`/`get_type`/`get_path`
    surface that `RequestExecutor.log_key` and `ChainLineBuilder.start`
    read for naming verbose-mode log files/lines.
    """

    id: uuid.UUID
    raw_type: str

    @property
    def get_id(self) -> str:
        return self.id.hex

    @property
    def get_type(self) -> str:
        return self.raw_type

    @property
    def get_path(self) -> None:
        return None


def edit_log_identifier_from_asset(asset: "Asset") -> AssetLogIdentifier:
    """Build the (id, raw_type) label `edit()` reports itself under in logs.

    Uses `Asset.internal_type` (the raw response wrapper key) rather than a
    validated `AssetTypes` value, since this is never used to build a
    request URL — only to name/label verbose-mode logs.
    """
    return AssetLogIdentifier(
        id=uuid.UUID(asset.get("id")),
        raw_type=asset.internal_type,
    )


# ----- Helper Models (support response parsing) -----
# NOTE: placed here (ahead of Asset/Message/Response Containers below) because
# those classes reference PageConfiguration/Audit/etc. directly in type
# annotations, which Python evaluates immediately at class-definition time.


class ReadOnlyPageConfigError(AttributeError):
    """Raised on any attempt to modify a `PageConfiguration` or `PageRegion`."""


REGION_READ_ONLY_MESSAGE = (
    "cannot make direct edits to a page's configuration region. Editing "
    "regions is only allowed at the template level: edit the `template` "
    "asset's `pageRegions` instead"
)
CONFIGURATION_READ_ONLY_MESSAGE = (
    "cannot make direct edits to a page's configuration. Edit the "
    "`pageConfigurationSet` asset's `pageConfiguration` instead (or the "
    "`template` asset's `pageRegions` for regions)"
)

_PAGE_VIEW_CONFIG = ConfigDict(populate_by_name=True, frozen=True)


@pydantic_dataclass(config=_PAGE_VIEW_CONFIG)
class PageRegion:
    """Read-only snapshot of one region of a page configuration.

    Cascade ignores region edits sent through a page's `edit()`; regions can
    only be changed on the `template` asset. Any assignment or deletion raises
    `ReadOnlyPageConfigError`, and nothing here is ever written back to the
    asset.

    Do not trust Cascade's `noBlock`/`noFormat` flags: they have been observed
    as `false` on regions with no `blockId`/`blockPath` or `formatId`/
    `formatPath`. Check the ids/paths themselves.
    """

    name: str
    block_id: str | None = Field(
        default=None, validation_alias="blockId", serialization_alias="blockId"
    )
    block_path: str | None = Field(
        default=None, validation_alias="blockPath", serialization_alias="blockPath"
    )
    block_recycled: bool | None = Field(
        default=None, validation_alias="blockRecycled", serialization_alias="blockRecycled"
    )
    no_block: bool | None = Field(
        default=None, validation_alias="noBlock", serialization_alias="noBlock"
    )
    format_id: str | None = Field(
        default=None, validation_alias="formatId", serialization_alias="formatId"
    )
    format_path: str | None = Field(
        default=None, validation_alias="formatPath", serialization_alias="formatPath"
    )
    format_recycled: bool | None = Field(
        default=None, validation_alias="formatRecycled", serialization_alias="formatRecycled"
    )
    no_format: bool | None = Field(
        default=None, validation_alias="noFormat", serialization_alias="noFormat"
    )
    id: str | None = None


@pydantic_dataclass(config=_PAGE_VIEW_CONFIG)
class PageConfiguration:
    """Read-only snapshot of one page configuration (e.g. `ASPX`, `XML`).

    Edit configurations on the `pageConfigurationSet` asset and regions on the
    `template` asset. Any assignment or deletion raises
    `ReadOnlyPageConfigError`. See `PageRegion` for why Cascade's own flags
    must not be trusted.
    """

    name: str
    default_configuration: bool | None = Field(
        default=None, validation_alias="defaultConfiguration", serialization_alias="defaultConfiguration"
    )
    template_id: str | None = Field(
        default=None, validation_alias="templateId", serialization_alias="templateId"
    )
    template_path: str | None = Field(
        default=None, validation_alias="templatePath", serialization_alias="templatePath"
    )
    format_recycled: bool | None = Field(
        default=None, validation_alias="formatRecycled", serialization_alias="formatRecycled"
    )
    page_regions: tuple[PageRegion, ...] = Field(
        default=(), validation_alias="pageRegions", serialization_alias="pageRegions"
    )
    include_xml_declaration: bool | None = Field(
        default=None, validation_alias="includeXMLDeclaration", serialization_alias="includeXMLDeclaration"
    )
    publishable: bool | None = None
    id: str | None = None


def _read_only(message: str) -> Callable[..., NoReturn]:
    def blocked(self: object, key: str, *value: object) -> NoReturn:
        raise ReadOnlyPageConfigError(message)

    return blocked


# `frozen=True` already blocks writes, but a frozen dataclass forbids defining
# `__setattr__` in its body and raises a generic FrozenInstanceError. Replace
# the generated methods after the fact so the error says where to edit.
PageRegion.__setattr__ = PageRegion.__delattr__ = _read_only(  # type: ignore[assignment,method-assign]
    REGION_READ_ONLY_MESSAGE
)
PageConfiguration.__setattr__ = PageConfiguration.__delattr__ = _read_only(  # type: ignore[assignment,method-assign]
    CONFIGURATION_READ_ONLY_MESSAGE
)


_page_configuration_adapter: TypeAdapter[PageConfiguration] = TypeAdapter(
    PageConfiguration
)


def _build_page_views(raw_configs: list[dict[str, Any]]) -> list[PageConfiguration]:
    """Parse raw `pageConfigurations` dicts into read-only snapshots.

    Raises pydantic ValidationError on a malformed configuration.
    """
    return [_page_configuration_adapter.validate_python(c) for c in raw_configs]


"""
Used to retrieve the workflow deinitions on assets
"""


class WorkflowSettingsModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    identifier: IdentifierType
    workflow_definitions: list[IdentifierType] = Field(
        validation_alias="workflowDefinitions", serialization_alias="workflowDefinitions"
    )
    inherited_workflow_definitions: list[IdentifierType] = Field(
        validation_alias="inheritedWorkflowDefinitions", serialization_alias="inheritedWorkflowDefinitions"
    )
    inherit_workflows: bool = Field(validation_alias="inheritWorkflows", serialization_alias="inheritWorkflows")
    require_workflow: bool = Field(validation_alias="requireWorkflow", serialization_alias="requireWorkflow")


class workflowSettingsPayload(SimplePayload):

    body: WorkflowSettingsModel = Field(..., validation_alias="workflowSettings", serialization_alias="workflowSettings")
    apply_inherit_workflows_to_children: bool | None = Field(
        default=False, validation_alias="applyInheritWorkflowsToChildren", serialization_alias="applyInheritWorkflowsToChildren"
    )
    apply_require_workflow_to_children: bool | None = Field(
        default=False, validation_alias="applyRequireWorkflowToChildren", serialization_alias="applyRequireWorkflowToChildren"
    )
    # __model__ = WorkflowSettingsModel


class Entries(BaseModel):
    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    level: Literal["none", "read", "write"]
    entry_type: IdentityTypes = Field(validation_alias="type", serialization_alias="type")
    name: str


class AccessRightsModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    user_id: IdentifierType = Field(validation_alias="identifier", serialization_alias="identifier")
    acl_entries: list[Entries] = Field(validation_alias="aclEntries", serialization_alias="aclEntries")
    all_level: Literal["none", "read", "write"] = Field(validation_alias="allLevel", serialization_alias="allLevel")


class accessRightsInformationPayload(SimplePayload):
    body: AccessRightsModel = Field(default=..., validation_alias="accessRightsInformation", serialization_alias="accessRightsInformation")
    apply_to_children: bool | None = Field(default=False, validation_alias="applyToChildren", serialization_alias="applyToChildren")


class WorkflowAction(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    action_identifier: str = Field(validation_alias="identifier", serialization_alias="identifier")
    label: str
    action_type: str = Field(validation_alias="actionType", serialization_alias="actionType")
    next_id: uuid.UUID = Field(validation_alias="nextId", serialization_alias="nextId")


class WorkflowSteps(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    step_identifier: str = Field(validation_alias="identifier", serialization_alias="identifier")
    label: str
    step_type: str = Field(validation_alias="stepType", serialization_alias="stepType")
    actions: list[WorkflowAction]
    owner: str | None


class workflowInformation(BaseModel):
    """Response from the `readWorkflowInformation` operation, describing an
    asset's active workflow instance and its steps/actions."""

    model_config = ConfigDict(frozen=True)

    related_entity: Annotated[IdentifierType, Field(validation_alias="relatedEntity", serialization_alias="relatedEntity")]
    current_step: Annotated[str, Field(validation_alias="currentStep", serialization_alias="currentStep")]
    ordered_steps: list[WorkflowSteps]
    unordered_steps: list[WorkflowSteps]
    start_date: datetime
    end_date: datetime
    name: str
    workflow_info_id: Annotated[uuid.UUID, Field(validation_alias="workflowInfoId", serialization_alias="workflowInfoId")]


class Audit(BaseModel):
    model_config = ConfigDict(frozen=True)

    user: str
    action: AuditTypes
    identifier: IdentifierType
    date: datetime


# ----- Core Response Models (cont'd) -----


class Asset:
    """Dynamic wrapper around a raw Cascade asset JSON payload.

    Cascade asset payloads have the shape `{"asset": {"<type>": {...}}}`
    with a structure that varies per asset type, so unlike the Pydantic
    models above this is a thin dict-backed wrapper rather than a fixed
    schema: `_data` holds the inner `{...}` dict by reference, and
    `__setattr__` only enforces that an existing field keeps its Python
    type when reassigned (it does not validate against a schema).
    `pageConfigurations`, if present, is parsed into `PageConfiguration`
    models up front (failing fast on a malformed one) for access via
    `get_page_configuration`. Those views are read-only snapshots: region
    and configuration edits are not sent on `edit()`.
    """

    _asset_type: str
    _data: dict[str, Any]
    _page_configs: list[PageConfiguration]

    def __init__(self, data: dict):
        object.__setattr__(self, "_asset_type", next(iter(data["asset"].keys())))
        inner: dict[str, Any] = data["asset"][self._asset_type]
        object.__setattr__(self, "_data", inner)

        # Parse pageConfigurations into Pydantic models
        object.__setattr__(self, "_page_configs", self._current_page_views())

    def _current_page_views(self) -> list[PageConfiguration]:
        if "pageConfigurations" not in self._data:
            return []
        return _build_page_views(self._data["pageConfigurations"])

    def __setattr__(self, key: str, value: object) -> None:
        if key.startswith("_"):
            object.__setattr__(self, key, value)
            return
        if key == "pageConfigurations":
            raise ReadOnlyPageConfigError(CONFIGURATION_READ_ONLY_MESSAGE)
        if key in self._data:
            current = self._data[key]
            if type(value) is not type(current):
                raise TypeError(
                    f"Field {key!r} has changed type: "
                    f"expected {type(current).__name__!r}, "
                    f"got {type(value).__name__!r}"
                )
        self._data[key] = value

    @property
    def internal_type(self) -> str:
        """The raw Cascade response wrapper key, lowercased.

        Not a validated `AssetTypes` value — Cascade's wrapper key doesn't
        always match the request-side type (e.g. `"scriptformat"` for a
        `format`/`format_SCRIPT` asset). This is only ever round-tripped back
        into a request body (see `AssetAdapter.dump_json`), which is why no
        normalization happens here; callers that need a validated
        `AssetTypes` must supply/derive one themselves.
        """
        return self._asset_type.lower()

    def get(self, key: str, max_depth=5):
        keys = key.split(".")

        if keys[0] in ("structuredData", "pageConfigurations"):
            warnings.warn("Use the designated functions for structuredData and ...")
        if len(keys) > max_depth:
            raise ValueError(f"{key} exceeds max depth of {max_depth}")

        def recursive(data, index=0):
            if index == len(keys):
                return data
            cur_search_node = keys[index]

            if not isinstance(data, dict):
                raise KeyError(f"Cannot traverse into non-dict value at '{cur_search_node}'")

            if cur_search_node not in data:
                raise KeyError(f"{cur_search_node} not in data")

            return recursive(data[cur_search_node], index=index + 1)
        return recursive(self._data)

    def get_data_structure(self: 'Asset', group: str, identifier: str) -> list[dict[str, Any]] | None:
        """
        Find nodes matching identifier within all instances of a group.
        Returns first match per group instance as a list of node objects by reference.
        Raises KeyError if required fields (identifier, structuredDataNodes) are missing.
        """

        def find_group(obj):
            if isinstance(obj, dict):
                if obj.get("type") == "group" and obj.get("identifier") == group:
                    yield obj
                for value in obj.values():
                    yield from find_group(value)
            elif isinstance(obj, list):
                for item in obj:
                    yield from find_group(item)

        def find_in_nodes(nodes):
            for node in nodes:
                try:
                    if (
                        node["identifier"] == identifier
                        and "structuredDataNodes" not in node
                    ):
                        return node
                    if "structuredDataNodes" in node:
                        result = find_in_nodes(node["structuredDataNodes"])
                        if result:
                            return result
                except KeyError as e:
                    raise KeyError(f"Missing required field in node: {e}")
            return None

        matches = []
        for group_node in find_group(self._data):
            try:
                nodes = group_node["structuredDataNodes"]
                match = find_in_nodes(nodes)
                if match:
                    matches.append(match)
            except KeyError as e:
                raise KeyError(f"Missing required field 'structuredDataNodes' in group node: {e}")

        return matches if matches else None

    def get_page_configuration(
        self, configuration_name: str, page_region: str | None = None
    ) -> PageConfiguration | PageRegion | None:
        """
        Find a page configuration and optionally a specific region within it.

        The returned models are read-only snapshots of the asset's current
        data, rebuilt on every call. Assigning to them (or to
        `pageConfigurations`) raises `ReadOnlyPageConfigError`: Cascade ignores
        region edits sent through `edit()`. Edit configurations on the
        `pageConfigurationSet` asset and regions on the `template` asset.

        Do not trust Cascade's `noBlock`/`noFormat` flags; they can read
        `false` on regions with no block/format ids or paths. A misspelled
        name returns None. Raises pydantic ValidationError if the raw data is
        malformed.

        Args:
            configuration_name: The 'name' of the configuration e.g. 'ASPX', 'XML'
            page_region:        The 'name' of the page region e.g. 'DEFAULT', 'FOOTER' (optional)

        Returns:
            - PageConfiguration object if only configuration_name is provided
            - PageRegion object if page_region is also provided
            - None if either is not found
        """
        object.__setattr__(self, "_page_configs", self._current_page_views())
        try:
            config = next(
                (c for c in self._page_configs if c.name == configuration_name), None
            )
        except (KeyError, AttributeError) as e:
            raise KeyError(f"Missing required field 'name' in PageConfiguration: {e}")

        if config is None:
            return None

        if page_region is None:
            return config

        try:
            region = next((r for r in config.page_regions if r.name == page_region), None)
        except (KeyError, AttributeError) as e:
            raise KeyError(f"Missing required field 'pageRegions' or 'name' in PageRegion: {e}")

        return region

    # Field names Cascade exposes on a site asset for the root container of
    # each asset type. Only asset types confirmed against a real site payload
    # are listed here; unmapped types return None rather than guess.
    _ROOT_CONTAINER_FIELDS: ClassVar[dict[str, str]] = {
        "datadefinition": "rootDataDefinitionContainerId",
        "sharedfield": "rootSharedFieldContainerId",
        "folder": "rootFolderId",
    }

    def root_container_id(self, asset_type: "AssetTypes") -> uuid.UUID | None:
        """Return the root container id for `asset_type` on this site asset.

        `self` must be a `site` asset. Returns None if there's no known root
        field for `asset_type` (see `_ROOT_CONTAINER_FIELDS`).
        """
        field = self._ROOT_CONTAINER_FIELDS.get(asset_type)
        if field is None:
            return None
        value = self._data.get(field)
        if value is None:
            return None
        return uuid.UUID(value)


class Message(SimplePayload):
    """A Cascade inbox message, also used as the payload for mark/delete-message operations."""

    model_config = ConfigDict(populate_by_name=True)

    m_from: Annotated[str, Field(validation_alias="from", serialization_alias="from", exclude=True)]
    m_to: Annotated[str, Field(validation_alias="to", serialization_alias="to", exclude=True)]
    m_subject: Annotated[str, Field(validation_alias="subject", serialization_alias="subject", exclude=True)]
    m_date: Annotated[datetime, Field(validation_alias="date", serialization_alias="date", exclude=True)]
    m_id: Annotated[uuid.UUID, Field(validation_alias="id", serialization_alias="id", exclude=True)]
    marked: str = Field("unread", validation_alias="markType", serialization_alias="markType")

    @field_validator("m_date", mode="after")
    @classmethod
    def remove_timezone(cls, dt: datetime) -> datetime:
        return dt.replace(tzinfo=None)


# ----- Response Containers -----


class CheckedOutAsset(BaseModel):
    """Response from the `checkOut` operation, referencing the new working copy."""

    model_config = ConfigDict(frozen=True)

    working_copy_identifier: IdentifierType = Field(validation_alias="workingCopyIdentifier", serialization_alias="workingCopyIdentifier")


class ListElements(BaseModel):
    """Response container for list-shaped endpoints (search, listSites, listMessages,
    readAudits, listSubscribers), whose JSON key varies by endpoint but is always
    aliased into `elements` via `AliasChoices`."""

    model_config = ConfigDict(frozen=True)
    elements: list[IdentifierType | Message | Audit] = Field(
        validation_alias=AliasChoices(
            "preferences",
            "matches",
            "messages",
            "relationships",
            "sites",
            "audits",
        )
    )

    @property
    def flat(self) -> list[IdentifierType | Message | Audit]:
        return self.elements


class CascadeError(BaseModel):
    """Represents a Cascade API-level failure response (`{"success": false, "message": ...}`)."""

    model_config = ConfigDict(frozen=True, extra='forbid')
    success: Literal[False] = False
    message: str = ""


class CascadeSuccess(BaseModel):
    """Represents a Cascade API-level success response with no further data (`{"success": true}`)."""

    model_config = ConfigDict(frozen=True, extra='forbid')
    success: Literal[True] = True


# ----- Parameter Payloads (sent to specific endpoints) -----


class SearchInformation(SimplePayload):
    """Payload for the `search` operation."""

    site_name: str = Field(validation_alias="siteName", serialization_alias="siteName")
    search_terms: str = Field(validation_alias="searchTerms", serialization_alias="searchTerms")
    search_fields: list[FieldsSearchTypes] | list[Literal[""]] = Field(
        validation_alias="searchFields", serialization_alias="searchFields",
        default_factory=lambda: [cast(Literal[""], "")]
    )
    search_types: list[AssetTypes] | list[Literal[""]] = Field(
        validation_alias="searchTypes", serialization_alias="searchTypes",
        default_factory=lambda: [cast(Literal[""], "")]
    )


class preference(SimplePayload):
    """Payload for the `editPreference` operation (a single user preference name/value)."""

    name: str
    value: str | None


class deleteParameters(SimplePayload):
    """Payload for the `delete` operation."""

    do_workflow: bool = Field(validation_alias="doWorkflow", serialization_alias="doWorkflow")
    destinations_identifiers: list[IdentifierType] = Field(validation_alias="destinations", serialization_alias="destinations")
    unpublish: bool = True


class copyParameters(SimplePayload):
    """Payload for the `copy` operation."""

    do_workflow: Annotated[bool, Field(validation_alias="doWorkflow", serialization_alias="doWorkflow")]
    new_name: Annotated[str, Field(default=..., validation_alias="newName", serialization_alias="newName")]
    destination_container_identifier: Annotated[
        IdentifierType, Field(validation_alias="destinationContainerIdentifier", serialization_alias="destinationContainerIdentifier")  # required
    ]


class moveParameters(SimplePayload):
    """Payload for the `move` operation."""

    destinations: list[IdentifierType]
    do_workflow: bool = Field(validation_alias="doWorkflow", serialization_alias="doWorkflow")
    destination_container_identifier: IdentifierType = Field(
        validation_alias="destinationContainerIdentifier", serialization_alias="destinationContainerIdentifier"
    )
    new_name: str = Field(default="", validation_alias="newName", serialization_alias="newName")  # empty new name means no rename
    unpublish: bool = True


class publishInformation(SimplePayload):
    """Payload for the `publish` operation."""

    unpublish: bool = True


class Comment(SimplePayload):
    """Payload for the `checkIn` operation (a check-in comment)."""

    comment: str


class SiteCopyParameter(SimplePayload):
    """Payload for the `siteCopy` operation."""

    original_sitename: str | IdentifierType = Field(validation_alias="originalSiteName", serialization_alias="originalSiteName")
    new_sitename: str = Field(validation_alias="newSiteName", serialization_alias="newSiteName")


class workflowTransitionInformation(SimplePayload):
    """Payload for the `performWorkflowTransition` operation."""

    workflow_identifier: Annotated[uuid.UUID, Field(validation_alias="workflowId", serialization_alias="workflowId")]
    action_identifier: Annotated[str, Field(validation_alias="actionIdentifier", serialization_alias="actionIdentifier")]
    transition_comment: str | None = Field(validation_alias="transitionComment", serialization_alias="transitionComment")


class auditParameters(SimplePayload):
    """Payload for the `readAudits` operation."""

    audit_type: AuditTypes = Field(validation_alias="auditType", serialization_alias="auditType")
    by_username: str | None = Field(default=None, validation_alias="username", serialization_alias="username")
    by_group: str | None = Field(default=None, validation_alias="groupname", serialization_alias="groupname")
    by_role: str | None = Field(default=None, validation_alias="rolename", serialization_alias="rolename")
    start_date: datetime | None = Field(default=None, validation_alias="startDate", serialization_alias="startDate")
    end_date: datetime | None = Field(default=None, validation_alias="endDate", serialization_alias="endDate")

    # Cascade needs at least one of username / groupname / rolename
    @model_validator(mode="after")
    def requires_username_group_or_role(self) -> Self:
        if not (self.by_username or self.by_group or self.by_role):
            raise ValueError("Provide at least one of username, groupname, or rolename.")
        return self

    """
    def toJson(self) -> str:
        return self.model_dump_json(
            by_alias=True,
            exclude_none=True,
        )
    """


"""
PAYLOAD_STATIC_REF = Union[
    deleteParameters,
    copyParameters,
    moveParameters,
    publishInformation,
    SearchInformation,
    preference,
    workflowSettingsPayload,
    Comment,
    auditParameters,
    SiteCopyParameter,
    accessRightsInformationPayload,
    Message,
    CheckedOutAsset,
    SimplePayload
]

"""


# ===== TYPE ADAPTERS (Model Serialization/Deserialization) =====

# ----- Request Payload Adapters -----
simple_payload_adapter = TypeAdapter(SimplePayload)
new_asset_adapter = TypeAdapter(NewAsset)

# ----- Response Model Adapters -----
list_element_adapter = TypeAdapter(ListElements)
identifier_type_adapter = TypeAdapter(IdentifierType)
access_rights_adapter = TypeAdapter(accessRightsInformationPayload)
workflow_settings_adapter = TypeAdapter(workflowSettingsPayload)
checked_out_adapter = TypeAdapter(CheckedOutAsset)
workflow_info_adapter = TypeAdapter(workflowInformation)
cascade_success_adapter = TypeAdapter(CascadeSuccess)


# `Payloads` is hoisted out of the Type Aliases section (which the guide places
# at the very end of the file) because serialize_payload's signature below
# references it, and Python evaluates parameter annotations eagerly at def time.
Payloads = SimplePayload | Asset


# ===== ENCODING LAYER (Serialize payloads to JSON, deserialize responses) =====


class AssetAdapter:
    """Drop-in counterpart to TypeAdapter for Asset objects.

    Mirrors TypeAdapter's validate_json / dump_json interface so callers
    never need isinstance checks to decide how to serialize/deserialize.
    """

    def validate_json(self, json_str: bytes | str) -> Asset:
        return Asset(json.loads(json_str))

    def dump_json(self, asset: Asset) -> bytes:
        # `_data` is what gets serialized. The `_page_configs` models are
        # read-only snapshots and are never written back. The raw dicts keep
        # every field the models don't parse (templateId, blockId,
        # formatId, ...), so round-tripping preserves them.
        data = {**asset._data}
        reconstructed = {"asset": {asset._asset_type: data}}
        return json.dumps(reconstructed).encode()


# asset_adapter lives here (not in the Type Adapters section above) because it
# requires the AssetAdapter class defined immediately above it.
asset_adapter = AssetAdapter()


def serialize_payload(payload: Payloads) -> bytes:
    """Serialize a request payload to JSON bytes, dispatching by payload type."""
    if isinstance(payload, Asset):
        return asset_adapter.dump_json(payload)
    elif isinstance(payload, NewAsset):
        return new_asset_adapter.dump_json(payload, by_alias=True)
    return simple_payload_adapter.dump_json(payload)


# ===== PARSER FRAMEWORK & RESPONSE PARSING =====


class ResponseParser[T](BaseModel):
    """Parses a raw response body, trying `CascadeError` first and falling
    back to `serializer` on the expected success shape.

    `_content` holds the parsed result (either a `CascadeError` or a `T`).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    serializer: TypeAdapter[Any] | AssetAdapter
    _content: T | CascadeError | None = PrivateAttr(default=None)

    def __init__(
        self,
        raw: bytes,
        serializer: TypeAdapter[Any] | AssetAdapter,
        **kwargs,
    ):
        super().__init__(serializer=serializer, **kwargs)
        try:
            self._content = CascadeError.model_validate_json(raw)
        except ValidationError:
            self._content = self.serializer.validate_json(raw)  # type: ignore[assignment]


# ----- Parser Functions -----


def parse_assets(raw: bytes) -> ResponseParser[Asset]:
    """Parse a `read` response body into an `Asset`."""
    a: ResponseParser[Asset] = ResponseParser(raw=raw, serializer=asset_adapter)
    return a


def parse_list_elements(raw: bytes) -> ResponseParser[ListElements]:
    """Parse a list-shaped response body (search, listSites, etc.) into `ListElements`."""
    a: ResponseParser[ListElements] = ResponseParser(raw, serializer=list_element_adapter)
    return a


def parse_payloads(raw: bytes) -> ResponseParser[SimplePayload]:
    """Parse a generic response body into the appropriate `SimplePayload` subclass."""
    return ResponseParser(raw=raw, serializer=simple_payload_adapter)


def parse_create_asset(raw: bytes, pass_type: str) -> ResponseParser[IdentifierType]:
    """Parse a `create` response, rebuilding an `IdentifierType` from `createdAssetId`.

    Cascade's create response only returns the new asset's id, not its
    type, so `pass_type` (the `asset_type` from the original create
    payload, bound via `functools.partial` in `Operations.create`) is
    injected to reconstruct a full `IdentifierType`.
    """
    data = json.loads(raw)
    if data.get("createdAssetId") is not None: # we know that the creation succeeded
        identifier_payload = {"id": data["createdAssetId"], "type": pass_type}
    
        return ResponseParser(
            json.dumps(identifier_payload).encode(),
            serializer=identifier_type_adapter,
        )
    return ResponseParser( # `createdAssetId` does NOT exist we know that its most likely a error
        raw,
        serializer=identifier_type_adapter,
    )


def parse_access_rights(raw: bytes) -> ResponseParser[accessRightsInformationPayload]:
    """Parse a `readAccessRights` response body."""
    return ResponseParser(raw=raw, serializer=access_rights_adapter)


def parse_workflow_settings(raw: bytes) -> ResponseParser[workflowSettingsPayload]:
    """Parse a `readWorkflowSettings` response body."""
    return ResponseParser(raw=raw, serializer=workflow_settings_adapter)


def parse_checked_out_asset(raw: bytes) -> ResponseParser[CheckedOutAsset]:
    """Parse a `checkOut` response body."""
    return ResponseParser(raw=raw, serializer=checked_out_adapter)


def parse_workflow_information(raw: bytes) -> ResponseParser[workflowInformation]:
    """Parse a `readWorkflowInformation` response body."""
    return ResponseParser(raw=raw, serializer=workflow_info_adapter)


def parse_success(raw: bytes) -> ResponseParser[CascadeSuccess]:
    """Parse a bare `{"success": true}` response body from a write operation."""
    return ResponseParser(raw=raw, serializer=cascade_success_adapter)


# ===== TYPE ALIASES (Convenience types for type hints) =====

CascadeObjects = ListElements | Payloads | CascadeError | CascadeSuccess
