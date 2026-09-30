import json
from uuid import UUID

import pytest

from cascade_cms.cmstypes import (
    Asset,
    AssetAdapter,
    IdentifierType,
    ListElements,
    Path,
    deleteParameters,
    edit_log_identifier_from_asset,
    moveParameters,
    resolve_identifier,
)


def test_resolve_identifier_from_identifier_type(page_identifier):
    assert resolve_identifier(page_identifier) == (
        "page",
        "8b320f55ac1001062545a6d2562cee4b",
    )


def test_resolve_identifier_from_path():
    path = Path(
        path="/cms/index",
        siteId=UUID("8b320f55ac1001062545a6d2562cee4b"),
        siteName="www.csi.edu",
        asset_type="page",
    )
    assert resolve_identifier(path) == ("page", "www.csi.edu", "/cms/index")


def test_resolve_identifier_path_requires_sitename():
    path = Path(
        path="/cms/index",
        siteId=UUID("8b320f55ac1001062545a6d2562cee4b"),
        asset_type="page",
    )
    with pytest.raises(ValueError):
        resolve_identifier(path)


def test_resolve_identifier_path_without_site_id():
    # PathBase.siteId is NotRequired: resolve_identifier never reads it.
    path = Path(
        path="/cms/index",
        siteName="www.csi.edu",
        asset_type="page",
    )
    assert resolve_identifier(path) == ("page", "www.csi.edu", "/cms/index")


def test_move_parameters_aliases_nested_identifier():
    destination = IdentifierType(
        identifier=UUID("8b320f55ac1001062545a6d2562cee4b"),
        asset_type="folder",
    )
    payload = moveParameters(
        destinations=[destination],
        do_workflow=False,
        destination_container_identifier=destination,
        new_name="",
        unpublish=False,
    )
    dumped = payload.model_dump()["moveParameters"]

    assert dumped["destinationContainerIdentifier"] == {
        "id": destination.identifier.hex,
        "type": "folder",
        "recycled": None,
        "path": None,
    }
    assert dumped["destinations"] == [
        {
            "id": destination.identifier.hex,
            "type": "folder",
            "recycled": None,
            "path": None,
        }
    ]


def test_delete_parameters_aliases_nested_identifiers():
    destination = IdentifierType(
        identifier=UUID("8b320f55ac1001062545a6d2562cee4b"),
        asset_type="folder",
    )
    payload = deleteParameters(
        do_workflow=False,
        destinations_identifiers=[destination],
        unpublish=False,
    )
    dumped = payload.model_dump()["deleteParameters"]

    assert dumped["destinations"] == [
        {
            "id": destination.identifier.hex,
            "type": "folder",
            "recycled": None,
            "path": None,
        }
    ]


def _raw_asset(**page_config_extra):
    return {
        "asset": {
            "page": {
                "id": "8b320f55ac1001062545a6d2562cee4b",
                "name": "index",
                "pageConfigurations": [
                    {
                        "name": "Default",
                        "templateId": "aaaa0f55ac1001062545a6d2562cee4",
                        "blockId": "bbbb0f55ac1001062545a6d2562cee4",
                        "formatId": "cccc0f55ac1001062545a6d2562cee4",
                        "pageRegions": [
                            {"name": "DEFAULT", "content": "<p>hi</p>"},
                        ],
                        **page_config_extra,
                    }
                ],
            }
        }
    }


def test_asset_dump_json_preserves_page_configuration_bindings():
    asset = Asset(_raw_asset())
    dumped = json.loads(AssetAdapter().dump_json(asset))
    config = dumped["asset"]["page"]["pageConfigurations"][0]

    assert config["templateId"] == "aaaa0f55ac1001062545a6d2562cee4"
    assert config["blockId"] == "bbbb0f55ac1001062545a6d2562cee4"
    assert config["formatId"] == "cccc0f55ac1001062545a6d2562cee4"
    assert config["pageRegions"] == [{"name": "DEFAULT", "content": "<p>hi</p>"}]


def test_list_elements_parses_list_sites_response():
    payload = {
        "sites": [
            {"id": "8b320f55ac1001062545a6d2562cee4b", "type": "site"},
        ]
    }
    parsed = ListElements.model_validate(payload)
    assert len(parsed.elements) == 1
    assert parsed.elements[0].get_type == "site"


@pytest.mark.parametrize(
    ("raw_key", "expected"),
    [
        ("datadefinition", "datadefinition"),
        ("dataDefinition", "datadefinition"),
        ("sharedField", "sharedfield"),
        ("scriptFormat", "scriptformat"),
    ],
)
def test_internal_type_is_raw_response_key(raw_key, expected):
    asset = Asset({"asset": {raw_key: {"id": "x"}}})
    assert asset.internal_type == expected


def test_edit_log_identifier_from_asset_builds_id_and_raw_type():
    asset = Asset(
        {
            "asset": {
                "page": {
                    "id": "8b320f55ac1001062545a6d2562cee4b",
                    "path": "mysite/blog/post-1",
                    "siteId": "9c431066bd21120736f6b7e3673dff5c",
                    "siteName": "mysite",
                }
            }
        }
    )
    identifier = edit_log_identifier_from_asset(asset)

    assert identifier.id == UUID("8b320f55ac1001062545a6d2562cee4b")
    assert identifier.raw_type == "page"
    assert identifier.get_type == "page"
    assert identifier.get_path is None


def test_edit_log_identifier_from_asset_without_site_fields():
    """siteId/siteName are absent from the response — edit_log_identifier_from_asset
    should not choke on their absence; it only ever reads id/internal_type."""
    asset = Asset(
        {
            "asset": {
                "folder": {
                    "id": "8b320f55ac1001062545a6d2562cee4b",
                    "path": "mysite/blog",
                }
            }
        }
    )
    identifier = edit_log_identifier_from_asset(asset)

    assert identifier.id == UUID("8b320f55ac1001062545a6d2562cee4b")
    assert identifier.raw_type == "folder"


def test_edit_log_identifier_from_asset_missing_id_raises():
    """A missing id fails loudly with KeyError rather than silently
    returning a bogus identifier."""
    asset = Asset({"asset": {"page": {"path": "mysite/blog/post-1"}}})

    with pytest.raises(KeyError):
        edit_log_identifier_from_asset(asset)


def test_asset_root_container_id_known_and_unknown_types():
    site = Asset(
        {
            "asset": {
                "site": {
                    "rootDataDefinitionContainerId": "8b320f55ac1001062545a6d2562cee4b",
                    "rootFolderId": "8b320f55ac1001062545a6d2562cee4c",
                }
            }
        }
    )
    assert site.root_container_id("datadefinition") == UUID(
        "8b320f55ac1001062545a6d2562cee4b"
    )
    assert site.root_container_id("folder") == UUID(
        "8b320f55ac1001062545a6d2562cee4c"
    )
    assert site.root_container_id("sharedfield") is None
    assert site.root_container_id("template") is None


def test_audit_parameters_accept_plain_strings():
    from cascade_cms.cmstypes import auditParameters

    for kw, key in (("username", "username"), ("groupname", "groupname"), ("rolename", "rolename")):
        params = auditParameters(audit_type="login", **{kw: "x"})
        assert params.model_dump(by_alias=True)["auditParameters"][key] == "x"


def test_audit_parameters_require_a_name():
    import pytest
    from pydantic import ValidationError

    from cascade_cms.cmstypes import auditParameters

    with pytest.raises(ValidationError):
        auditParameters(audit_type="login")


def test_new_asset_accepts_snake_and_camel_and_serializes_camel():
    import json

    from cascade_cms.cmstypes import NewAsset

    sid = "8b320f55ac1001062545a6d2562cee4b"
    snake = NewAsset(name="n", asset_type="page", site_id=sid, parent_folder_path="/")
    camel = NewAsset(name="n", asset_type="page", siteId=sid, parentFolderPath="/")
    for asset in (snake, camel):
        body = json.loads(asset.dump_json())["asset"]["page"]
        assert body["siteId"] == sid
        assert body["parentFolderPath"] == "/"
        assert "site_id" not in body


def test_search_information_snake_fields_serialize_camel():
    from cascade_cms.cmstypes import SearchInformation

    dumped = SearchInformation(site_name="s", search_terms="t").model_dump(by_alias=True)
    body = dumped["searchInformation"]
    assert body["siteName"] == "s" and body["searchTerms"] == "t"


def test_path_accepts_snake_and_camel_and_stays_hashable():
    from cascade_cms.cmstypes import Path as CascadePath

    camel = CascadePath(path="/a", siteName="s", asset_type="page")
    snake = CascadePath(path="/a", site_name="s", asset_type="page")
    assert camel == snake and hash(camel) == hash(snake)
    assert camel.get_type == "page" and camel.get_id is None


def test_identifier_type_nested_path_is_model():
    ident = IdentifierType(
        id="8b320f55ac1001062545a6d2562cee4b",
        type="page",
        path={"path": "a/b", "siteName": "s", "siteId": "9c431066bd21120736f6b7e3673dff5c"},
    )
    assert ident.get_path == "a/b" and ident.get_sitename == "s"
    dumped = ident.model_dump(by_alias=True)["path"]
    assert dumped["siteName"] == "s"
    assert dumped["siteId"] == "9c431066bd21120736f6b7e3673dff5c"


def test_workflow_settings_payload_roundtrip():
    from cascade_cms.cmstypes import workflowSettingsPayload

    ident = {"id": "8b320f55ac1001062545a6d2562cee4b", "type": "folder"}
    payload = workflowSettingsPayload.model_validate(
        {
            "workflowSettings": {
                "identifier": ident,
                "workflowDefinitions": [],
                "inheritedWorkflowDefinitions": [],
                "inheritWorkflows": True,
                "requireWorkflow": False,
            },
            "applyInheritWorkflowsToChildren": True,
        }
    )
    assert payload.body.identifier.get_type == "folder"
    assert payload.body.inherit_workflows is True
    body = payload.model_dump(by_alias=True)["workflowSettingsPayload"]
    assert body["workflowSettings"]["inheritWorkflows"] is True
    assert body["applyInheritWorkflowsToChildren"] is True


def test_access_rights_and_audit_models_validate():
    from cascade_cms.cmstypes import AccessRightsModel, Audit

    rights = AccessRightsModel.model_validate(
        {
            "identifier": {"id": "8b320f55ac1001062545a6d2562cee4b", "type": "user"},
            "aclEntries": [{"level": "read", "type": "group", "name": "g"}],
            "allLevel": "none",
        }
    )
    assert rights.acl_entries[0].entry_type == "group" and rights.all_level == "none"
    audit = Audit.model_validate(
        {
            "user": "u",
            "action": "login",
            "identifier": {"id": "8b320f55ac1001062545a6d2562cee4b", "type": "user"},
            "date": "2026-01-01T00:00:00Z",
        }
    )
    assert audit.user == "u"


def test_list_elements_accepts_audits_key():
    from cascade_cms.cmstypes import Audit

    ident = {"id": "8b320f55ac1001062545a6d2562cee4b", "type": "user"}
    result = ListElements.model_validate(
        {
            "audits": [
                {"user": "u", "action": "login", "identifier": ident, "date": "2026-01-01T00:00:00Z"}
            ]
        }
    )
    assert isinstance(result.elements[0], Audit)


def test_workflow_action_next_id_alias():
    from cascade_cms.cmstypes import WorkflowAction

    nid = "9c431066bd21120736f6b7e3673dff5c"
    for key in ("nextId", "next_id"):
        action = WorkflowAction.model_validate(
            {"identifier": "a", "label": "l", "actionType": "t", key: nid}
        )
        assert action.next_id.hex == nid


class TestAssetGetWarning:
    @staticmethod
    def _asset() -> Asset:
        return Asset({"asset": {"page": {"structuredData": {}, "pageConfigurations": []}}})

    def test_structured_data_warning_names_accessor(self):
        with pytest.warns(UserWarning, match=r"get_data_structure\(group") as caught:
            self._asset().get("structuredData")
        assert caught[0].filename == __file__

    def test_page_configurations_warning_names_accessor_and_read_only(self):
        with pytest.warns(UserWarning, match=r"read-only.*get_page_configuration") as caught:
            self._asset().get("pageConfigurations")
        assert caught[0].filename == __file__

    def test_other_keys_do_not_warn(self):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            Asset({"asset": {"page": {"name": "n"}}}).get("name")
