# cascade-cms

A typed, async REST client for Hannon Hill Cascade CMS.

## Usage

```python
from cascade_cms.cmstypes import Asset, IdentifierType
from cascade_cms.wrapper import CascadeWrapperBase

environment_variables = {
    "API_KEY": "...",
    "CASCADE_URL": "...",
}
configuration_variables = {
    "cache_name": "./cache/cache.sqlite",
    "allowed_codes": (200,),
    "allowed_methods": ("GET",),
}

with CascadeWrapperBase(environment_variables, configuration_variables) as cascade:
    identifier = IdentifierType(identifier="e868f539ac1001062cfa029c4c5df4d0", asset_type="folder")
    cascade.operations.read(identifier)
    results = cascade.submit_requests(Asset)
```

Operations that take an identifier (`read`, `delete`, `copy`, `move`, `publish`, `checkIn`, `checkOut`,
`listSubscribers`, `readAccessRights`, `readWorkflowSettings`, `readWorkflowInformation`,
`performWorkflowTransition`) accept either an `IdentifierType` (asset type + UUID) or a `Path`
(asset type + site name + site-relative path) — see `cascade_cms.cmstypes.resolve_identifier`.

See `examples/read_and_update_asset.py` for a fuller walkthrough.

## Development

```bash
pip install -e ".[dev]"
pytest
```
