# Changelog

All notable changes to this project will be documented in this file.

## [3.0.0]

### Changed
- **Operations now build linked chains instead of one shared queue.** Every `cascade.operations.<op>()` call starts an `OperationChain` and returns it; `.then(callback)` and further operation calls append steps to that chain. Steps run in order, each receiving the previous step's result, so `read → transform → edit → publish` works in a single batch.
- **`submit_requests()` returns one result per chain, in the order the chains were built.** Failures are included as values rather than dropped: a `CascadeError` when the API rejects a request, or the exception object a callback raised. Callers no longer need to match responses back to requests by hand.
- Chains run concurrently (bounded by the driver's `MAX_REQUESTS`), so batching still costs one round of requests; only the steps within a chain are sequential.
- The progress meter now counts chains rather than individual requests, and its failure count is per batch instead of cumulative.

### Added
- `OperationChain` and `Node` (`cascade_cms.operations`), plus `execute()` / `execute_async()` for running a single chain directly.
- `edit()` accepts a callable as its payload; it is invoked with the previous step's result, which is how a transformed asset is written back.
- Chain-aware logging: `log_chain_start`, `log_node_execution`, `log_chain_error`, and `log_chain_complete` record which chain, which step, and which node failed.
- `CascadeCMSRestDriver.execute_requests()` — executes a batch of requests and returns results in submission order, with failures included.
- Package-level re-exports: `from cascade_cms import CascadeWrapperBase, Operations, OperationChain, Node`.

### Fixed
- Callbacks no longer leak between batches. `Operations._callbacks` was never cleared, so a callback registered for one `submit_requests()` re-ran on every later one.
- Results are no longer silently dropped or returned in completion order.
- Two `OperationLogger`s created in the same second shared one underlying logger and cross-wrote into each other's logfiles.
- Importing `cascade_cms` no longer creates a `./cache/` directory; the default cache backend is now built when a driver is constructed.

### Removed
- `Operations.then()` and `Operations._callbacks` — callbacks attach to a chain now, not to the builder.
- `Operations._execute_callbacks_on_result()` and `CascadeWrapperBase._execute_all_callbacks()`.

### Breaking
- Operation methods return `OperationChain`, not `Operations`. Code that chained operations off `cascade.operations` (`cascade.operations.read(a).read(b)`) now extends one chain instead of queuing two independent requests; use two `cascade.operations.read(...)` calls for two chains.
- `edit(payload)` is now `edit(identifier, payload)`. The identifier is used for error reporting and does not change the request URL.
- `submit_requests()` returns one entry per chain including failures, where it previously returned one entry per successful request in completion order.
- Operations no longer append to `driver.pending_requests`; chains own their requests.

## [2.0.3]

### Fixed
- `parse_create_asset` no longer raises a `NameError` on `create()` error responses (e.g. invalid API key). It now checks for `createdAssetId` before rebuilding an `IdentifierType`, and falls through to `ResponseParser`'s error-first logic (`CascadeError`) when the field is absent, instead of surfacing as a swallowed empty result.

## [2.0.2]

### Fixed
- `SimplePayload.format_builder()` now respects field aliases during serialization, so payload subclasses (`copyParameters`, `deleteParameters`, `moveParameters`, `publishInformation`, etc.) emit camelCase keys instead of snake_case.
- `IdentifierType.identifier` now serializes as a bare 32-char hex string (instead of dashed UUID format) to match Cascade's REST API requirements.
- `CascadeWrapperBase` constructor was failing with `TypeError` due to `Operations` receiving `logger=` kwarg instead of matching its dataclass field name `_logger=`.
- Write operations (`edit`, `delete`, `copy`, `move`, `publish`, `checkIn`, `siteCopy`, `editAccessRights`, `markMessage`, `deleteMessage`, `editPreference`, `editWorkflowSettings`, `performWorkflowTransition`) now properly parse and surface bare `{"success": true}` responses instead of silently dropping them.

### Added
- `Asset.asset_type` public property getter to read the asset type without accessing the private `_asset_type` attribute.
- `CascadeSuccess` response model and `parse_success` parser for handling success-only responses from write operations.

## [2.0.0]

### Added
- `Path`-based asset addressing (`IdentifierType | Path`) across all identifier-accepting operations.
- Dedicated response parsers for `readAccessRights`, `readWorkflowSettings`, `checkOut`, and `readWorkflowInformation`.
- `performWorkflowTransition` implementation (previously a stub).
- `src/`-layout packaging via `pyproject.toml` (Hatchling build backend).

### Fixed
- `readWorkflowInformation` was calling the `readWorkflowSettings` endpoint instead of its own.

### Changed
- `cmstypes.py` reorganized into clearly separated payload models, response models, type adapters, and parsers.
