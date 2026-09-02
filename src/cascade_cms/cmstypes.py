
import json
import uuid
from datetime import datetime
from typing import (
    Annotated,
    Any,
    ClassVar,
    Literal,
    NotRequired,
    Self,
    TypedDict,
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


class PathBase(TypedDict):
    """
    Base class for Path object

    Attributes:
        path (str): The path string
        siteId (uuid_string): unique identifier string
                              of the site associated with it
        siteName (str):
    """

    path: str
    siteId: NotRequired[uuid.UUID]
    siteName: Annotated[str | None, Field(default=None)]


class Path(PathBase):
    """
    Represents a Path object that can be called directly.
    Substitution for TypeIdentifers with asset id
    Attributes:
        asset_type (AssetTypes): (Required) the type of the asset
    """

    asset_type: Literal[AssetTypes]


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
        try:
            fields_info = self.__pydantic_fields__  # Pydantic V3
        except AttributeError:
            fields_info = self.model_fields  # Pydantic V2

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
            (fields_info[name].alias or name): dump(value)
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
        validate_by_alias=False,
    )

    name: str
    asset_type: AssetTypes
    site_name: str | None = Field(default=None, alias="siteName")
    site_id: uuid.UUID | None = Field(default=None, alias="siteId")
    parent_folder_path: str | None = Field(default=None, alias="parentFolderPath")
    parent_folder_id: uuid.UUID | None = Field(default=None, alias="parentFolderId")

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

    identifier: Annotated[uuid.UUID, Field(alias='id')]
    asset_type: Annotated[AssetTypes, Field(default=..., alias="type")]
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
            return self.path["path"]

    @property
    def get_sitename(self):
        if self.path is not None:
            return self.path["siteName"]

    @property
    def get_site_id(self):
        # siteId is NotRequired (unlike siteName, it has no Field default),
        # so pydantic may not populate the key at all — plain indexing would
        # KeyError on that legitimate case.
        if self.path is not None:
            return self.path.get("siteId")

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
    if identifier.get("siteName") is None:
        raise ValueError("Path identifiers require siteName to build the request URL")
    return (str(identifier["asset_type"]), str(identifier["siteName"]), str(identifier["path"]))


def identifier_from_asset(asset: "Asset") -> IdentifierType:
    """Build an `IdentifierType` from an `Asset`'s own id/path/site fields.

    A Cascade asset payload carries its own identity as flat `_data` keys
    (`id`, `path`, `siteId`, `siteName`) rather than the nested `path` shape
    `IdentifierType.path` (`PathBase`) expects, so this reshapes one into the
    other. Used by `edit()` to derive a request's identifier from the asset
    being saved, rather than from a separately-supplied identifier argument.
    """
    path_value: PathBase = {
        "path": asset.get("path"),
        "siteName": asset._data.get("siteName"),
    }
    site_id = asset._data.get("siteId")
    if site_id:
        path_value["siteId"] = uuid.UUID(site_id)

    return IdentifierType(
        identifier=uuid.UUID(asset.get("id")),
        asset_type=cast(AssetTypes, asset.asset_type),
        path=path_value,
    )


# ----- Helper Models (support response parsing) -----
# NOTE: placed here (ahead of Asset/Message/Response Containers below) because
# those classes reference PageConfiguration/Audit/etc. directly in type
# annotations, which Python evaluates immediately at class-definition time.


class PageRegion(BaseModel):
    model_config = ConfigDict(
        validate_assignment=True,
        from_attributes=True,
    )

    name: str
    content: str | None = None


class PageConfiguration(BaseModel):
    model_config = ConfigDict(
        validate_assignment=True,
        from_attributes=True,
    )

    name: str
    pageRegions: list[PageRegion]


"""
Used to retrieve the workflow deinitions on assets
"""


class WorkflowSettingsModel(TypedDict):
    identifier: IdentifierType
    workflowDefinitions: list[IdentifierType]
    inheritedWorkflowDefinitions: list[IdentifierType]
    inheritWorkflows: bool
    requireWorkflow: bool


class workflowSettingsPayload(SimplePayload):

    body: WorkflowSettingsModel = Field(..., alias="workflowSettings")
    applyInheritWorkflowsToChildren: bool | None = False
    applyRequireWorkflowToChildren: bool | None = False
    # __model__ = WorkflowSettingsModel


class Entries(TypedDict):
    level: Literal["none", "read", "write"]
    entry_type: Annotated[IdentityTypes, Field(alias="type")]
    name: str


class AccessRightsModel(TypedDict):
    user_id: Annotated[IdentifierType, Field(alias="identifier")]
    acl_entries: Annotated[list[Entries], Field(alias="aclEntries")]
    allLevel: Literal["none", "read", "write"]


class accessRightsInformationPayload(SimplePayload):
    body: AccessRightsModel = Field(default=..., alias="accessRightsInformation")
    apply_to_children: bool | None = Field(default=False, alias="applyToChildren")


class WorkflowAction(TypedDict):
    action_identifier: Annotated[str, Field(alias="identifier")]
    label: str
    action_type: Annotated[str, Field(alias="actionType")]
    next_id: uuid.UUID


class WorkflowSteps(TypedDict):
    step_identifier: Annotated[str, Field(alias="identifier")]
    label: str
    step_type: Annotated[str, Field(alias="stepType")]
    actions: list[WorkflowAction]
    owner: str | None


class workflowInformation(BaseModel):
    """Response from the `readWorkflowInformation` operation, describing an
    asset's active workflow instance and its steps/actions."""

    model_config = ConfigDict(frozen=True)

    related_entity: Annotated[IdentifierType, Field(alias="relatedEntity")]
    current_step: Annotated[str, Field(alias="currentStep")]
    ordered_steps: list[WorkflowSteps]
    unordered_steps: list[WorkflowSteps]
    start_date: datetime
    end_date: datetime
    name: str
    workflow_info_id: Annotated[uuid.UUID, Field(alias="workflowInfoId")]


class Audit(TypedDict):
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
    models up front for convenient access via `get_page_configuration`.
    """

    _asset_type: str
    _data: dict[str, Any]
    _page_configs: list[PageConfiguration]

    def __init__(self, data: dict):
        object.__setattr__(self, "_asset_type", next(iter(data["asset"].keys())))
        inner: dict[str, Any] = data["asset"][self._asset_type]
        object.__setattr__(self, "_data", inner)

        # Parse pageConfigurations into Pydantic models
        if "pageConfigurations" in self._data:
            object.__setattr__(
                self,
                "_page_configs",
                [
                    PageConfiguration(**config)
                    for config in self._data["pageConfigurations"]
                ],
            )
        else:
            object.__setattr__(self, "_page_configs", [])

    def __setattr__(self, key: str, value: object) -> None:
        if key.startswith("_"):
            object.__setattr__(self, key, value)
            return
        if key in self._data:
            current = self._data[key]
            if type(value) is not type(current):
                raise TypeError(
                    f"Field {key!r} has changed type: "
                    f"expected {type(current).__name__!r}, "
                    f"got {type(value).__name__!r}"
                )
        self._data[key] = value

    # Cascade wraps a response under a key that's usually the requested
    # asset_type re-cased (e.g. "dataDefinition" for a "datadefinition"
    # request), but not always: this handles the exceptions where the
    # wrapper key isn't a mechanical re-casing of the request-side type.
    _ASSET_TYPE_KEY_ALIASES: ClassVar[dict[str, str]] = {
        "scriptformat": "format",
    }

    @property
    def asset_type(self) -> str:
        """The request-side asset type (matching `AssetTypes`/`Path.asset_type`),
        normalized from the raw response wrapper key in `_asset_type`."""
        lowered = self._asset_type.lower()
        return self._ASSET_TYPE_KEY_ALIASES.get(lowered, lowered)

    def get(self, key: str):
        """Access _data fields, raising KeyError if missing."""
        if key not in self._data:
            raise KeyError(f"Field '{key}' not found")
        return self._data[key]

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
        Returns Pydantic model objects by reference.
        Raises KeyError if required fields (name, pageRegions) are missing from models.

        Args:
            configuration_name: The 'name' of the configuration e.g. 'ASPX', 'XML'
            page_region:        The 'name' of the page region e.g. 'DEFAULT', 'FOOTER' (optional)

        Returns:
            - PageConfiguration object if only configuration_name is provided
            - PageRegion object if page_region is also provided
            - None if either is not found
        """
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
            region = next((r for r in config.pageRegions if r.name == page_region), None)
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

    m_from: Annotated[str, Field(alias="from", exclude=True)]
    m_to: Annotated[str, Field(alias="to", exclude=True)]
    m_subject: Annotated[str, Field(alias="subject", exclude=True)]
    m_date: Annotated[datetime, Field(alias="date", exclude=True)]
    m_id: Annotated[uuid.UUID, Field(alias="id", exclude=True)]
    marked: str = Field("unread", alias="markType")

    @field_validator("m_date", mode="after")
    @classmethod
    def remove_timezone(cls, dt: datetime) -> datetime:
        return dt.replace(tzinfo=None)


# ----- Response Containers -----


class CheckedOutAsset(BaseModel):
    """Response from the `checkOut` operation, referencing the new working copy."""

    model_config = ConfigDict(frozen=True)

    workingCopyIdentifier: IdentifierType


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
        )
    )

    @property
    def flat(self) -> list[IdentifierType | Message | Audit]:
        return self.elements


class CascadeError(BaseModel):
    """Represents a Cascade API-level failure response (`{"success": false, "message": ...}`)."""

    model_config = ConfigDict(frozen=True, extra='forbid')
    success: bool = False
    message: str = ""


class CascadeSuccess(BaseModel):
    """Represents a Cascade API-level success response with no further data (`{"success": true}`)."""

    model_config = ConfigDict(frozen=True)
    success: bool = True


# ----- Parameter Payloads (sent to specific endpoints) -----


class SearchInformation(SimplePayload):
    """Payload for the `search` operation."""

    siteName: str
    searchTerms: str
    searchFields: list[FieldsSearchTypes] | list[Literal[""]] = Field(
        default_factory=lambda: [cast(Literal[""], "")]
    )
    searchTypes: list[AssetTypes] | list[Literal[""]] = Field(
        default_factory=lambda: [cast(Literal[""], "")]
    )


class preference(SimplePayload):
    """Payload for the `editPreference` operation (a single user preference name/value)."""

    name: str
    value: str | None


class deleteParameters(SimplePayload):
    """Payload for the `delete` operation."""

    do_workflow: bool = Field(alias="doWorkflow")
    destinations_identifiers: list[IdentifierType] = Field(alias="destinations")
    unpublish: bool = True


class copyParameters(SimplePayload):
    """Payload for the `copy` operation."""

    do_workflow: Annotated[bool, Field(alias="doWorkflow")]
    new_name: Annotated[str, Field(default=..., alias="newName")]
    destination_container_identifier: Annotated[
        IdentifierType, Field(alias="destinationContainerIdentifier")  # required
    ]


class moveParameters(SimplePayload):
    """Payload for the `move` operation."""

    destinations: list[IdentifierType]
    do_workflow: bool = Field(alias="doWorkflow")
    destination_container_identifier: IdentifierType = Field(
        alias="destinationContainerIdentifier"
    )
    new_name: str = Field(default="", alias="newName")  # empty new name means no rename
    unpublish: bool = True


class publishInformation(SimplePayload):
    """Payload for the `publish` operation."""

    unpublish: bool = True


class Comment(SimplePayload):
    """Payload for the `checkIn` operation (a check-in comment)."""

    comment: str


class SiteCopyParameter(SimplePayload):
    """Payload for the `siteCopy` operation."""

    original_sitename: str | IdentifierType = Field(alias="originalSiteName")
    new_sitename: str = Field(alias="newSiteName")


class workflowTransitionInformation(SimplePayload):
    """Payload for the `performWorkflowTransition` operation."""

    workflow_identifier: Annotated[uuid.UUID, Field(alias="workflowId")]
    action_identifier: Annotated[str, Field(alias="actionIdentifier")]
    transition_comment: str | None = Field(alias="transitionComment")


class auditParameters(SimplePayload):
    """Payload for the `readAudits` operation."""

    auditType: AuditTypes
    by_identifier: IdentifierType = Field(alias="identifier")
    by_username: str | None = Field(default=None, alias="username")
    by_group: str | None = Field(default=None, alias="groupname")
    by_role: str | None = Field(default=None, alias="rolename")
    startDate: datetime | None = Field(default=None)
    endDate: datetime | None = Field(default=None)

    # make sure its only user, group, role
    @field_validator("by_identifier", mode="after")
    @classmethod
    def is_admin_entity(cls, identifier: IdentifierType) -> IdentifierType:
        if identifier.get_type not in {"user", "role", "group"}:
            raise ValueError("Identifier needs to be either user, role, or group.")
        return identifier

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
        # `_page_configs` only models `name`/`pageRegions[].content` for
        # convenient access via `get_page_configuration`; it is not
        # authoritative on write. `_data["pageConfigurations"]` (copied
        # below via `asset._data`) still holds the original raw dicts,
        # including fields the model doesn't parse (templateId, blockId,
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

    `_content` holds the parsed result (either a `CascadeError` or a `T`),
    and `_cacheable` is set to True only when the success-path parse
    succeeds, so error responses are never cached.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    serializer: TypeAdapter[Any] | AssetAdapter | None = None
    _content: T | CascadeError | None = PrivateAttr(default=None)
    _cacheable: bool = PrivateAttr(default=False)

    def __init__(
        self,
        raw: bytes,
        serializer: TypeAdapter[Any] | AssetAdapter | None = None,
        **kwargs,
    ):
        super().__init__(serializer=serializer, **kwargs)
        try:
            self._content = CascadeError.model_validate_json(raw)
        except ValidationError:
            if self.serializer is None:
                raise RuntimeWarning("No serializer included...")
            self._content = self.serializer.validate_json(raw)  # type: ignore[assignment]
            self._cacheable = True


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
