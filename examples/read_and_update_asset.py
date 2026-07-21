import os

from uuid import UUID
from cascade_cms.cmstypes import IdentifierType, SearchInformation, Asset, NewAsset
from cascade_cms.wrapper import CascadeWrapperBase

from pprint import pprint


def read_asset_and_create_placeholders() -> None:
    """Read a single Cascade asset and build simple placeholder values for common inputs."""

    environment_variables = {
        "API_KEY": os.environ["CASCADE_API_KEY"],
        "CASCADE_URL": os.environ["CASCADE_URL"],
    }
    configuration_variables = {
        "cache_name": "./cache/cache.sqlite",
        "allowed_codes": (200,),
        "allowed_methods": ("GET",),
    }

    obja = {
        "name":"cool_new_asset.txt",
        "asset_type":"file",
        "site_name":"wwwdev.csi.edu",
        "parent_folder_id":"4aa4251dac10010f1c30bf189a1e5619",
        "text":"Hello World! From Keith!"
    }

    shiny_asset = NewAsset(**obja)
    print(shiny_asset)
    asset_access = IdentifierType(identifier="e868f539ac1001062cfa029c4c5df4d0", asset_type="folder")

    searchinfo = SearchInformation(siteName="www.csi.edu", searchTerms="/cms/")

    read_id = IdentifierType(
        identifier=UUID("8b320f55ac1001062545a6d2562cee4b"),
        asset_type="page",
    )

    with CascadeWrapperBase(environment_variables, configuration_variables) as cascade:

        cascade.operations.read(read_id)
        results = cascade.submit_requests(Asset)
        asset = results[0]
        matches = asset.get_data_structure("main-content","content")
        if matches:
            node = matches[0]
            node['text'] = "<p>Hello World!</p>"

        cascade.operations.readAccessRights(asset_access)
        r = cascade.submit_requests()
        print(r)

        """
        cascade.operations.search(searchinfo)
        search_results = cascade.submit_requests()
        print(search_results)

        cascade.operations.create(shiny_asset)
        resulting = cascade.submit_requests(IdentifierType)
        my_asset_s = resulting[0]

        # read newly create asset
        cascade.operations.read(my_asset_s)
        r = cascade.submit_requests(Asset)

        """




if __name__ == "__main__":
    # read_asset_and_create_placeholders()
    read_asset_and_create_placeholders()
