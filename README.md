# cascade-cms

A typed, async REST client for Hannon Hill Cascade CMS.

[Full Documentation](https://sharkdroid.github.io/wiki/cascade-cms-wiki/)

## Usage

```python
from cascade_cms.cmstypes import Asset, IdentifierType
from cascade_cms.wrapper import CascadeWrapperBase

environment_variables = {
    "API_KEY": "...",
    "CASCADE_URL": "...",
    "SERVER": "prod",  # label used for logfile naming
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

### Operation chains

Every `cascade.operations.<op>()` call starts an **operation chain** and returns it. Chaining
`.then(callback)` or another operation onto it adds a step to *that* chain; a fresh
`cascade.operations.<op>()` call starts a separate one.

Steps inside a chain run strictly in order, each receiving the previous step's result, so a
read can be transformed and written back in one pass:

```python
def rewrite(asset):
    asset.keywords = "updated"
    return asset

with CascadeWrapperBase(environment_variables, configuration_variables) as cascade:
    cascade.operations.read(page_a).edit(page_a, rewrite).publish(page_a)
    cascade.operations.read(page_b).then(report)
    cascade.operations.delete(old_page)

    results = cascade.submit_requests()
```

- **Chains run concurrently**, so a batch still costs one round of requests, not one per chain.
  Only the steps *within* a chain are sequential.
- **One result per chain, in the order the chains were built** — `results[0]` belongs to the
  first chain. No more matching responses back to requests by hand.
- **Failures are values, not gaps.** A chain stops at its first failure and that object lands in
  the results: a `CascadeError` when the API rejects a request, or the exception a callback
  raised. Other chains are unaffected. Check with `isinstance(result, CascadeError)`.
- **A callback returning `None`** passes the previous result through, so side-effect callbacks
  (logging, reporting) don't break the chain.
- **`edit()` accepts a callable** as its payload; it is invoked with the previous step's result,
  which is how a transformed asset gets written back.
- Chains are cleared once `submit_requests()` returns, so a callback registered for one batch
  never re-runs in the next.

### Logging

`CascadeWrapperBase` accepts an optional third `debug` argument. Leaving it as `None`
(the default) runs in **normal mode**: a minimal console (`[INIT]`/`[RUNNING]`/`Processed: n/N`/
`[DONE]`/`[EXIT]`) plus a simple logfile at `./logs/{SERVER}_{timestamp}.log`. Passing a dict
switches to **debug mode**: a quiet console and a verbose, nested logfile at
`./logs/{SERVER}_debug_{timestamp}.log` describing every request, response, callback, and error.

```python
debug_config = {
    "log_dir": "./logs",
    "log_operations": True,
    "log_callbacks": True,
    "log_responses": True,
    "show_payload_data": True,
    "show_network_headers": False,
    "show_error_variables": True,
    "response_line_limit": 8,   # -1 = dump full response body
}

with CascadeWrapperBase(environment_variables, configuration_variables, debug=debug_config) as cascade:
    ...
```

All keys are required in debug mode — there are no inferred defaults, so you always know what
you opted into.

## Development

```bash
pip install -e ".[dev]"
pytest
```
