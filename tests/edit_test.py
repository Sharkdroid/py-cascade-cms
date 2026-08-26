from uuid import UUID

from cascade_cms.cmstypes import Asset, CascadeError, CascadeSuccess, IdentifierType
from cascade_cms.wrapper import CascadeWrapperBase, EnvironmentVars

"""
Title: Manual Human test - read + modify + edit child assets of a folder
Date: 8/25/26
Description: Read,Change,Edit a list of children asset from a folder

Expected Output:
CascadeSuccess()
CascadeSuccess()
CascadeSuccess()

Actual Output: 




"""

environmentConfig = EnvironmentVars(
    SERVER="APPTEST",
    API_KEY="...",
    CASCADE_URL="https://cascadeapptest.csi.edu:8443"
)

debug_config = {
    "log_dir": "./logs",
    "show_network_headers": False,
}

parent_folder = IdentifierType(
    identifier=UUID("3afed4e3ac10010f3e1865e7e983d4e1"),
    asset_type="folder"
)

def convert_children_identifier(asset: Asset) -> list[IdentifierType] | None:
    """
    Convert the Asset's children into `IdentifierType` refer to the [IMPROVEMENT] comment for more details
    """
    raw_children_data: list | None = asset.get("children")

    if raw_children_data is None:
        raise ValueError("raw_children_data: Returned a null value")

    return [IdentifierType(**child) for child in raw_children_data]

def change_displayname(single_asset: Asset) -> Asset:
    single_asset.displayName = "This is my perfect victory! That's right! I win!"
    return single_asset

try:
    with CascadeWrapperBase(
        environmentVariables=environmentConfig,
        configurationVariables=None,
        debug=debug_config
    ) as cascade:
        # Read all children assets of the `parent_folder`
        cascade.operations \
            .read(parent_folder) \
            .then(convert_children_identifier)

        folder_children = cascade.submit_requests(IdentifierType)

        if isinstance(folder_children, CascadeError | Exception):
            raise Exception(folder_children)

        folder_children = folder_children[0]  # get the list result from the first index

        cascade.operations \
            .read(folder_children) \
            .then(change_displayname)
        results = cascade.submit_requests(Asset)
except Exception as e:
    print(e)