import logging
from pprint import pprint

from typing_extensions import TypedDict
from pydantic import (
    AfterValidator,
    AliasChoices,
    AliasGenerator,
    BaseModel,
    TypeAdapter,
    ConfigDict,
    Field,
    FieldSerializationInfo,
    ModelWrapValidatorHandler,
    PrivateAttr,
    RootModel,
    PlainSerializer,
    SerializerFunctionWrapHandler,
    StringConstraints,
    DirectoryPath,
    TypeAdapter,
    AliasPath,
    field_serializer,
    model_serializer,
    ValidationError,
    create_model,
    field_validator,
    model_validator,
)

import uuid
from typing import (
    Annotated,
    Callable,
    Generic,
    List,
    Literal,
    Optional,
    Dict,
    Any,
    Self,
    Tuple,
    Type,
    TypeAlias,
    TypeVar,
    TypedDict,
    Union,
    ClassVar,
)

T = TypeVar("T")
from datetime import datetime
import json

# ----- TYPE ALIASES & HELPERS -----

IdentityTypes: TypeAlias = Literal[
    "group",
    "user",
    "role",
]


AssetTypes: TypeAlias = Literal[
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


FieldsSearchTypes: TypeAlias = Literal[
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


AuditTypes: TypeAlias = Literal[
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

# NOTE: DOES NOT CHECK IF THE ASSET IS ALREADY CHECKOUT ON CASCADE: ONLY TRACKS USAGE THROUGH LIBRARY
ALL_CHECKOUT_ASSETS: set[str] = set()


# ----- UTILITY FUNCTIONS -----


def reformat_name(class_name: str):
    if class_name[0].isupper():
        return class_name[0].lower() + class_name[1:]
    return class_name


def set_checkedout(key: str):
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
    siteId: uuid.UUID
    siteName: Annotated[Optional[str], Field(default=None)]


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
        return {reformat_name(subclass_name): self.__dict__}


class NewAsset(SimplePayload):
    model_config = ConfigDict(
        extra="allow",
        validate_by_name=True,
        validate_by_alias=False,
    )

    name: str
    asset_type: AssetTypes
    site_name: Optional[str] = Field(default=None, alias="siteName")
    site_id: Optional[uuid.UUID] = Field(default=None, alias="siteId")
    parent_folder_path: Optional[str] = Field(default=None, alias="parentFolderPath")
    parent_folder_id: Optional[uuid.UUID] = Field(default=None, alias="parentFolderId")

    @field_serializer("site_id", "parent_folder_id")
    def serialize_uuid_as_hex(self, value: Optional[uuid.UUID]) -> Optional[str]:
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
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
    )

    identifier: Annotated[uuid.UUID, Field(alias='id')]
    asset_type: Annotated[AssetTypes, Field(default=..., alias="type")]
    recycled: Annotated[Optional[bool], Field(default=None)] = None
    path: Annotated[Optional[PathBase], Field(default=None)] = None

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
        if self.path is not None:
            return self.path["siteId"]

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
        return (identifier.get_type, identifier.get_id)
    if identifier.get("siteName") is None:
        raise ValueError("Path identifiers require siteName to build the request URL")
    return (identifier["asset_type"], identifier["siteName"], identifier["path"])


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
    content: Optional[str] = None


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
    workflowDefinitions: List[IdentifierType]
    inheritedWorkflowDefinitions: List[IdentifierType]
    inheritWorkflows: bool
    requireWorkflow: bool


class workflowSettingsPayload(SimplePayload):

    body: WorkflowSettingsModel = Field(..., alias="workflowSettings")
    applyInheritWorkflowsToChildren: Optional[bool] = False
    applyRequireWorkflowToChildren: Optional[bool] = False
    # __model__ = WorkflowSettingsModel


class Entries(TypedDict):
    level: Literal["none", "read", "write"]
    entry_type: Annotated[IdentityTypes, Field(alias="type")]
    name: str


class AccessRightsModel(TypedDict):
    user_id: Annotated[IdentifierType, Field(alias="identifier")]
    acl_entries: Annotated[List[Entries], Field(alias="aclEntries")]
    allLevel: Literal["none", "read", "write"]


class accessRightsInformationPayload(SimplePayload):
    body: AccessRightsModel = Field(default=..., alias="accessRightsInformation")
    apply_to_children: Optional[bool] = Field(default=False, alias="applyToChildren")


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
    owner: Optional[str]


class workflowInformation(BaseModel):
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
    _asset_type: str
    _data: Dict[str, Any]
    _page_configs: list[PageConfiguration]

    def __init__(self, data: dict):
        object.__setattr__(self, "_asset_type", next(iter(data["asset"].keys())))
        inner: Dict[str, Any] = data["asset"][self._asset_type]
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

    def get(self, key: str, default=None):
        """Access _data fields conveniently."""
        return self._data.get(key, default)

    def get_data_structure(self: 'Asset', group: str, identifier: str) -> list[Dict[str, Any]] | None:
        """
        Find nodes matching identifier within all instances of a group.
        Returns first match per group instance as a list of node objects by reference.
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
                if (
                    node.get("identifier") == identifier
                    and "structuredDataNodes" not in node
                ):
                    return node
                if "structuredDataNodes" in node:
                    result = find_in_nodes(node["structuredDataNodes"])
                    if result:
                        return result
            return None

        matches = []
        for group_node in find_group(self._data):
            nodes = group_node.get("structuredDataNodes", [])
            match = find_in_nodes(nodes)
            if match:
                matches.append(match)

        return matches if matches else None

    def get_page_configuration(
        self, configuration_name: str, page_region: Optional[str] = None
    ) -> Optional[PageConfiguration | PageRegion]:
        """
        Find a page configuration and optionally a specific region within it.
        Returns Pydantic model objects by reference.

        Args:
            configuration_name: The 'name' of the configuration e.g. 'ASPX', 'XML'
            page_region:        The 'name' of the page region e.g. 'DEFAULT', 'FOOTER' (optional)

        Returns:
            - PageConfiguration object if only configuration_name is provided
            - PageRegion object if page_region is also provided
            - None if either is not found
        """
        config = next(
            (c for c in self._page_configs if c.name == configuration_name), None
        )

        if config is None:
            return None

        if page_region is None:
            return config

        region = next((r for r in config.pageRegions if r.name == page_region), None)

        return region


class Message(SimplePayload):
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
    model_config = ConfigDict(frozen=True)

    workingCopyIdentifier: IdentifierType


class ListElements(BaseModel):
    model_config = ConfigDict(frozen=True)
    elements: list[IdentifierType | Message | Audit] = Field(
        validation_alias=AliasChoices(
            "preferences",
            "matches",
            "messages",
            "relationships"
        )
    )

    @property
    def flat(self) -> list[IdentifierType | Message | Audit]:
        return self.elements


class CascadeError(BaseModel):
    model_config = ConfigDict(frozen=True)
    success: bool = False
    message: str


# ----- Parameter Payloads (sent to specific endpoints) -----


class SearchInformation(SimplePayload):
    siteName: str
    searchTerms: str
    searchFields: List[FieldsSearchTypes] | List[Literal[""]] = Field(
        default_factory=lambda: [""]
    )
    searchTypes: List[AssetTypes] | List[Literal[""]] = Field(
        default_factory=lambda: [""]
    )


class preference(SimplePayload):
    name: str
    value: Optional[str]


class deleteParameters(SimplePayload):
    do_workflow: bool = Field(alias="doWorkflow")
    destinations_identifiers: list[IdentifierType] = Field(alias="destinations")
    unpublish: bool = True


class copyParameters(SimplePayload):
    do_workflow: Annotated[bool, Field(alias="doWorkflow")]
    new_name: Annotated[str, Field(default=..., alias="newName")]
    destination_container_identifier: Annotated[
        IdentifierType, Field(alias="destinationContainerIdentifier")  # required
    ]


class moveParameters(SimplePayload):
    destinations: list[IdentifierType]
    do_workflow: bool = Field(alias="doWorkflow")
    destination_container_identifier: IdentifierType = Field(
        alias="destinationContainerIdentifier"
    )
    new_name: str = Field(default="", alias="newName")  # empty new name means no rename
    unpublish: bool = True


class publishInformation(SimplePayload):
    unpublish: bool = True


class Comment(SimplePayload):
    comment: str


class SiteCopyParameter(SimplePayload):
    original_sitename: str | IdentifierType = Field(alias="originalSiteName")
    new_sitename: str = Field(alias="newSiteName")


class workflowTransitionInformation(SimplePayload):
    workflow_identifier: Annotated[uuid.UUID, Field(alias="workflowId")]
    action_identifier: Annotated[str, Field(alias="actionIdentifier")]
    transition_comment: Optional[str] = Field(alias="transitionComment")

    # TODO LATER: could add a model_validator after to make sure the uuid is coming from a Action


class auditParameters(SimplePayload):
    auditType: AuditTypes
    by_identifier: IdentifierType = Field(alias="identifier")
    by_username: Optional[str] = Field(default=None, alias="username")
    by_group: Optional[str] = Field(default=None, alias="groupname")
    by_role: Optional[str] = Field(default=None, alias="rolename")
    startDate: Optional[datetime] = Field(default=None)
    endDate: Optional[datetime] = Field(default=None)

    # make sure its only user, group, role
    @field_validator("by_identifier", mode="after")
    @classmethod
    def is_admin_entity(cls, identifier: IdentifierType) -> IdentifierType:
        if identifier.get_type not in {"user", "role", "group"}:
            raise ValueError(f"Identifier needs to be either user, role, or group.")
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
        page_configs = [
            {
                "name": c.name,
                "pageRegions": [
                    {"name": r.name, "content": r.content} for r in c.pageRegions
                ],
            }
            for c in asset._page_configs
        ]
        data = {**asset._data}
        if page_configs:
            data["pageConfigurations"] = page_configs
        reconstructed = {"asset": {asset._asset_type: data}}
        return json.dumps(reconstructed).encode()


# asset_adapter lives here (not in the Type Adapters section above) because it
# requires the AssetAdapter class defined immediately above it.
asset_adapter = AssetAdapter()


def serialize_payload(payload: Payloads) -> bytes:
    if isinstance(payload, Asset):
        return asset_adapter.dump_json(payload)
    elif isinstance(payload, NewAsset):
        return new_asset_adapter.dump_json(payload, by_alias=True)
    return simple_payload_adapter.dump_json(payload)


# ===== PARSER FRAMEWORK & RESPONSE PARSING =====


class ResponseParser(BaseModel, Generic[T]):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    serializer: Optional[TypeAdapter[Any] | AssetAdapter] = None
    _content: T | CascadeError | None = PrivateAttr(default=None)
    _cacheable: bool = PrivateAttr(default=False)

    def __init__(
        self,
        raw: bytes,
        serializer: Optional[TypeAdapter[Any] | AssetAdapter] = None,
        **kwargs,
    ):
        super().__init__(serializer=serializer, **kwargs)
        try:
            self._content = CascadeError.model_validate_json(raw)
        except ValidationError:
            if self.serializer is None:
                RuntimeWarning("No serializer included...")
            self._content = self.serializer.validate_json(raw)  # type: ignore[assignment]
            self._cacheable = True


# ----- Parser Functions -----


def parse_assets(raw: bytes) -> ResponseParser[Asset]:
    a = ResponseParser(raw=raw, serializer=asset_adapter)
    return a


def parse_list_elements(raw: bytes) -> ResponseParser[ListElements]:
    a = ResponseParser(raw, serializer=list_element_adapter)
    return a


def parse_payloads(raw: bytes) -> ResponseParser[SimplePayload]:
    return ResponseParser(raw=raw, serializer=simple_payload_adapter)


def parse_create_asset(raw: bytes, pass_type: str) -> ResponseParser[IdentifierType]:
    data = json.loads(raw)
    identifier_payload = {"id": data["createdAssetId"], "type": pass_type}
    return ResponseParser(
        json.dumps(identifier_payload).encode(),
        serializer=identifier_type_adapter,
    )


def parse_access_rights(raw: bytes) -> ResponseParser[accessRightsInformationPayload]:
    return ResponseParser(raw=raw, serializer=access_rights_adapter)


def parse_workflow_settings(raw: bytes) -> ResponseParser[workflowSettingsPayload]:
    return ResponseParser(raw=raw, serializer=workflow_settings_adapter)


def parse_checked_out_asset(raw: bytes) -> ResponseParser[CheckedOutAsset]:
    return ResponseParser(raw=raw, serializer=checked_out_adapter)


def parse_workflow_information(raw: bytes) -> ResponseParser[workflowInformation]:
    return ResponseParser(raw=raw, serializer=workflow_info_adapter)


# ===== TYPE ALIASES (Convenience types for type hints) =====

CascadeObjects = ListElements | Payloads | CascadeError
