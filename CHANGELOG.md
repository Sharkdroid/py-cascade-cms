# Changelog

All notable changes to this project will be documented in this file.

## [3.3.0]

### Removed
- **BREAKING: response caching is removed entirely** (no persistent and no in-memory cache): `CacheHandler`, `default_cache_backend()`, `OperationLogger.log_cache_hit()` and `ResponseParser._cacheable`. No `./cache` directory is created.
- **BREAKING: `configurationVariables` is removed from `CascadeWrapperBase`** with no deprecation period. `CascadeWrapperBase(environmentVariables, debug=None, *, exit_on_failure=True, log_dir=None)`; `exit_on_failure` is now keyword-only.
- **BREAKING: `backendConfig` is removed from `CascadeCMSRestDriver`.**
- **BREAKING: `python-dotenv` is no longer a dependency**, and `aiohttp-client-cache` is dropped. `aiohttp` is now a direct dependency (`>=3.14.3`).

### Changed
- **BREAKING: status lines go to stderr instead of stdout** (`[INIT]`, `[DEBUG]`, `[DONE]`, the tally, `[EXIT]`, console `[ERROR]`). Anything reading the tally from stdout must read stderr or the logfile.
- **BREAKING: a non-200 HTTP status is now a `CascadeError` (category CASCADE, message `"<status> <reason>"`)**, no longer a NETWORK failure. The response body is never read or logged.
- **BREAKING: `ResponseParser.serializer` is now required**; the `RuntimeWarning` branch is gone.
- **BREAKING: `RequestExecutor.parser` is now required** (the unused default was removed). `RequestExecutor` is exported from `cascade_cms/__init__.py`.
- Log filenames now include microseconds and a per-process serial (`{SERVER}[_debug]_{timestamp}_{n}.log`), so two wrappers created in the same second never share a file.
- A batch-level failure's prefix follows its cause: `[NETWORK]` for network causes, `[CASCADE-REST-CMS]` otherwise.
- `CascadeCMSRestDriver.close()` clears the closed event loop as the current loop.
- Release process: the tag-triggered workflow no longer creates a GitHub release (PyPI publishing on tag push is unchanged, with `contents: read`), and `PUBLISHING.md` no longer has a manual `twine upload` step.

### Added
- `log_dir` keyword-only parameter on `CascadeWrapperBase` (normal and debug mode; created if missing; wins over a debug config's `log_dir`).
- `[LOG]: <path>` is printed to stderr right after `[INIT]`.
- The logfile records the tally line and a final `[EXIT-CODE]: <outcome>` line.
- `cascade_cms.redaction.mask_token()`; the debug logfile masks the `Authorization` header to its last four characters (`Bearer ****abcd`).

### Fixed
- Each wrapper closes and releases its logfile handle on exit.
- Verified (Python 3.12): Cascade's `Partitioned` session cookie no longer breaks requests now that the cache library is gone; no compatibility patch is needed in the library.

**Files changed:** `src/cascade_cms/driver.py`, `src/cascade_cms/wrapper.py`, `src/cascade_cms/operation_logger.py`, `src/cascade_cms/cmstypes.py`, `src/cascade_cms/redaction.py` (added), `pyproject.toml`, `.github/workflows/release.yml`, `PUBLISHING.md`, `README.md`, `AGENTS.md`, `tests/test_driver.py`, `tests/test_non200.py` (added), `tests/test_redaction.py` (added), `tests/test_logging_exit.py` (added), `tests/edit_test.py`

## [3.2.2]

### Added
- `create()` and `delete()` accept a callable, like `edit()`: it receives the previous node's result and returns the `NewAsset`(s) / identifier(s), so `read → then → create → delete` can be one chain.

### Fixed
- `ListElements` now accepts the `audits` key, so `readAudits` responses parse.
- `WorkflowAction.next_id` now validates from the `nextId` key Cascade sends.
- A deferred (callable-payload) node that resolves to several requests now keeps every result instead of only the first, so partial failures no longer stop the chain (also applies to `edit`).

### Changed
- **`ChainResults.ok` renamed to `ChainResults.success`** (`.failed` is unchanged; no `.ok` alias is kept). The batch console line still reports `N/M succeeded`, where N is the number of chains without a failure, i.e. `len(results.success)` for single-result chains.
- **Snake_case field names on every Pydantic model.** camelCase is now only the wire name: accepted when validating (`validation_alias`) and still written on requests (`serialization_alias`), so request bodies are unchanged. Renamed: `PageConfiguration.pageRegions` → `page_regions`; `CheckedOutAsset.workingCopyIdentifier` → `working_copy_identifier`; `SearchInformation` `siteName`/`searchTerms`/`searchFields`/`searchTypes` → `site_name`/`search_terms`/`search_fields`/`search_types`; `auditParameters` `auditType`/`startDate`/`endDate` → `audit_type`/`start_date`/`end_date`; `workflowSettingsPayload` `apply*ToChildren` → `apply_*_to_children`. Existing `alias=` fields moved to explicit validation/serialization aliases so type checkers see the snake_case constructor names. `NewAsset` now validates by alias as well as by name (`site_id=` and `siteId=` both work). The former `TypedDict` shapes are now frozen/validated Pydantic models with snake_case attributes: `PathBase`/`Path` (`site_id`, `site_name`; `Path` also exposes `get_id`/`get_type`/`get_path`), `WorkflowSettingsModel`, `Entries`, `AccessRightsModel` (`all_level`), `WorkflowAction`, `WorkflowSteps`, `Audit`. Dict-style access (`path["siteName"]`, `payload.body["identifier"]`) becomes attribute access, and `PathBase.site_id` now serializes as bare hex like `IdentifierType`.
- **`auditParameters` no longer takes an `IdentifierType`.** `readAudits` now takes plain strings via `username`, `groupname`, or `rolename`; at least one is required. Auditing by entity identifier is no longer supported through this payload.

**Files changed:** `src/cascade_cms/cmstypes.py`, `src/cascade_cms/operations.py`, `src/cascade_cms/failures.py`, `src/cascade_cms/wrapper.py`, `README.md`, `tests/test_cmstypes.py`, `tests/test_operation_chains.py`, `tests/test_wrapper.py`, `pyproject.toml`

## [3.2.1]

### Fixed
- `OperationLogger.log_python_error()`'s console `[ERROR]` line now also
  carries the `[NETWORK]`/`[CASCADE-REST-CMS]` prefix for a batch-level
  failure — per A1, the prefix was already applied to the logfile's
  `!ERROR:` line in 3.2.0, but not to the console line.

**Files changed:** `src/cascade_cms/operation_logger.py`,
`tests/test_operation_logger.py`, `tests/test_wrapper.py`

## [3.2.0]

Failure handling moves out of user scripts and into the library. No more
`try/except` around `submit_requests()`, no more `isinstance(result,
CascadeError)` checks — `CascadeWrapperBase`'s context manager now owns
failure reporting and the script's exit code.

### Added
- `cascade_cms.failures` (new module, not re-exported from
  `cascade_cms/__init__.py` — import it directly): `FailureCategory`
  (`CASCADE`/`NETWORK`/`LIBRARY`/`CALLBACK`), `ChainFailure` (records
  where and why a chain stopped: chain index, identifier, step, step
  name, node type, category, the original error, and a display message),
  `classify_failure()`, and `CascadeBatchError`.
- `submit_requests()` now returns `ChainResults` — a `list` subclass
  (indexing/iteration/`len()` unchanged) with `.ok` (results from chains
  that didn't fail) and `.failed` (a `ChainFailure` per failed chain).
- `CascadeWrapperBase.__init__` gains `exit_on_failure: bool = True`. With
  the default, `__exit__` ends the script with a non-zero exit code when
  anything failed — a callback exception is re-raised unwrapped, any other
  recorded failure raises `SystemExit(1)`. Code after the `with` block
  does not run in either case. Pass `False` to embed the wrapper in a
  longer-lived process (e.g. an MCP server), where `__exit__` never
  raises.
- The logfile's `!ERROR:` line is now prefixed `[NETWORK]` for a
  network-layer failure or `[CASCADE-REST-CMS]` for any other library-side
  failure at an operation step; a `CascadeError` or a callback exception
  gets no prefix.
- `OperationLogger.log_exit()` now always prints a cumulative tally —
  `"{failed} failed, {succeeded} succeeded"` — appending `": reference log
  for details"` only when at least one failure came from the API, the
  network, or the library itself (not a callback-only failure).

### Changed
- `submit_requests()` no longer swallows a batch-level failure and returns
  an empty list — it raises `CascadeBatchError` (chained `from` the
  original exception) instead. An empty chain queue still returns an
  empty `ChainResults`.

### Removed
- The dead `RuntimeWarning` carve-out in `CascadeWrapperBase.__exit__`
  (it compared the exception *class* against `RuntimeWarning` with
  `isinstance()`, which can never be true, so the "documented" carve-out
  never actually fired).
- `OperationLogger`'s old `[ERRORS]: N failure(s) — check <logfile>` exit
  line — replaced by the tally line above.

### Breaking
- A batch-level failure now raises `CascadeBatchError` instead of
  `submit_requests()` returning `[]`.
- `CascadeWrapperBase.__init__` defaults `exit_on_failure=True`: a script
  that previously relied on inspecting `submit_requests()`'s return value
  itself and continuing past the `with` block regardless of failures will
  now exit non-zero (or have a callback exception re-raised) when
  anything failed, instead of continuing.

**Files changed:** `src/cascade_cms/failures.py` (added),
`src/cascade_cms/operations.py`, `src/cascade_cms/wrapper.py`,
`src/cascade_cms/operation_logger.py`, `tests/test_wrapper.py` (added),
`tests/test_operation_chains.py`, `tests/test_operation_logger.py`,
`README.md`

## [3.1.3]

`skill/` and the in-progress MCP server moved to their own repo, `cascade-cms-tools`, keeping this repo focused on just the library. Added a `py.typed` marker so external type checkers can resolve this library's types.

**Files changed:** `skill/` (removed), `src/cascade_cms/mcp/` (removed), `tests/mcp/` (removed), `src/cascade_cms/py.typed` (added), `MCP_IMPLEMENTATION_PLAN_REV.md` (removed), `pyproject.toml`, `.gitignore`, `AGENTS.md`

## [3.1.2]

`Asset.get()` now raises `KeyError` instead of silently returning `None`; `Asset.asset_type` renamed to `internal_type` (dropping the `scriptformat` normalization); `identifier_from_asset()` renamed to `edit_log_identifier_from_asset()` and now returns a lightweight `AssetLogIdentifier` instead of a full `IdentifierType`.

**Files changed:** `src/cascade_cms/cmstypes.py`, `src/cascade_cms/operations.py`

## [3.1.1]

### Fixed
- `OperationLogger.log_cache_hit()` now logs when a request is served from the local response cache (debug mode only). Previously, cached responses were silently treated as real network requests in the chain walk, making it impossible to distinguish a stale cache from a broken request or a real response.

### Added
- `OperationLogger.log_cache_hit(method, url)` — writes `[CACHED-{method}] {url}` after the request's `[METHOD] URL` line in debug logs, marking responses served from cache instead of the network.

**Files changed:** `src/cascade_cms/driver.py`, `src/cascade_cms/operation_logger.py`

## [3.1.0]

### Changed
- **`edit()` no longer takes an `identifier` argument** — `operations.edit(payload, parser)` / `chain.edit(payload, parser)`, not `edit(identifier, payload, parser)`. Each asset's own `id`/`path`/`site` fields are used to derive its request identifier (`cmstypes.identifier_from_asset()`), including for logging/error reporting.
- **A list of identifiers passed to `read`, `delete`, `copy`, `move`, `publish`, `checkIn`, `checkOut`, `listSubscribers`, or `readAccessRights` now fans out into one independent chain per identifier** (a `ChainGroup`), instead of batching all identifiers into a single node/chain. A failure on one asset no longer aborts the others; `.then()`/further operation calls on the returned `ChainGroup` apply to every chain in the group. `submit_requests()` now returns one result per identifier for these calls, not one result for the whole batch.
- **`OperationLogger` rewritten around one pipeline-style line per chain** (e.g. `(id, type) READ -> transform: Asset -> EDIT -> CascadeSuccess`), written once when a chain finishes or stops, instead of nested per-operation/per-callback blocks. A stopped chain gets a `v` marker and `!ERROR:` block aligned under the failing step. Verbose (debug) mode now writes per-request `{key}_request.json`/`{key}_response.json` files under the log directory instead of inlining payloads/responses in the logfile.
- `CascadeWrapperBase.submit_requests()` now reports a `succeeded/total` tally per batch (`log_batch_start`/`log_batch_end`) instead of a running "Processed: n/total" counter.

### Fixed
- `IdentifierType.get_site_id` no longer raises `KeyError` when a `Path` has no `siteId` — it was read with plain indexing despite `siteId` being `NotRequired`.
- `CascadeError` now sets `extra='forbid'` and defaults `message` to `""`, so a malformed or minimal error response can't silently pass validation with missing/extra fields.

### Removed
- `CascadeCMSRestDriver.pending_requests`, `request_buffer`, `process_executors()`, and the dead `isFlushed` attribute — chains own their requests directly via `execute_requests()`/`_submitRequests(requests)`; the no-arg legacy path is gone.
- `OperationLogger.operation_scope()`, `callback_scope()`, `log_operation()`, `log_callbacks()`, `log_running()`, and the depth/indent stack — superseded by the per-chain pipeline line.

### Added
- `cmstypes.identifier_from_asset()` — builds an `IdentifierType` from an `Asset`'s own `id`/`path`/`siteId`/`siteName` fields.
- `operations.ChainGroup` — forwards `.then()` and every `OperationChain` method to each chain produced by a fanned-out list-of-identifiers call.
- `wrapper.EnvironmentVars` — a `TypedDict` documenting the required `SERVER`/`API_KEY`/`CASCADE_URL` keys for `CascadeWrapperBase`'s `environmentVariables` argument.

### Breaking
- `edit(identifier, payload, parser=...)` → `edit(payload, parser=...)`.
- `operations.read/delete/copy/move/publish/checkIn/checkOut/listSubscribers/readAccessRights` return a `ChainGroup` (not a single `OperationChain`) when passed a list of identifiers, and `submit_requests()` returns one result per identifier instead of one result for the batch.

**Files changed:** `src/cascade_cms/cmstypes.py`, `src/cascade_cms/driver.py`, `src/cascade_cms/operation_logger.py`, `src/cascade_cms/operations.py`, `src/cascade_cms/wrapper.py`, `pyproject.toml`, `tests/test_cmstypes.py`, `tests/test_operation_chains.py`, `tests/test_operation_logger.py`

## [3.0.1]

### Fixed
- `SimplePayload.format_builder()` now recurses into nested `BaseModel`/`list[BaseModel]` field values (e.g. an `IdentifierType` nested inside `moveParameters`, `deleteParameters`, `copyParameters`, `SiteCopyParameter`, `auditParameters`), so they serialize under their aliases (`id`/`type`) instead of their Python field names (`identifier`/`asset_type`). Previously only top-level fields were aliased.
- `AssetAdapter.dump_json()` no longer rebuilds `pageConfigurations` from the parsed `PageConfiguration` models (which only carry `name` and `pageRegions[].content`). It now serializes `asset._data` verbatim, so `templateId`/`blockId`/`formatId` and any other fields Cascade sends survive an `edit()` round-trip instead of being silently dropped.
- `ListElements.elements`'s `AliasChoices` now includes `"sites"`, so `listSites()` responses parse instead of raising and being dropped by the driver.
- `PathBase.siteId` is now `NotRequired` — `resolve_identifier()` never reads it, so a `Path` built without it no longer trips type checkers over an unused required field.

### Added
- `Asset.asset_type` now normalizes the raw response wrapper key (e.g. `"dataDefinition"`, `"scriptFormat"`) to the request-side type (`"datadefinition"`, `"format"`) instead of returning it verbatim.
- `Asset.root_container_id(asset_type)` — looks up a site asset's root container id for a given asset type (currently covers `datadefinition`, `sharedfield`, `folder`; returns `None` for unmapped types), so callers no longer need to hand-carry the `root*ContainerId` field-name table themselves.

**Files changed:** `src/cascade_cms/cmstypes.py`, `src/cascade_cms/wrapper.py`, `tests/test_cmstypes.py`

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

**Files changed:** `src/cascade_cms/__init__.py`, `src/cascade_cms/driver.py`, `src/cascade_cms/operation_logger.py`, `src/cascade_cms/operations.py`, `src/cascade_cms/wrapper.py`, `tests/test_operation_chains.py`, `tests/test_operations.py`

## [2.0.3]

### Fixed
- `parse_create_asset` no longer raises a `NameError` on `create()` error responses (e.g. invalid API key). It now checks for `createdAssetId` before rebuilding an `IdentifierType`, and falls through to `ResponseParser`'s error-first logic (`CascadeError`) when the field is absent, instead of surfacing as a swallowed empty result.

**Files changed:** `src/cascade_cms/cmstypes.py`

## [2.0.2]

### Fixed
- `SimplePayload.format_builder()` now respects field aliases during serialization, so payload subclasses (`copyParameters`, `deleteParameters`, `moveParameters`, `publishInformation`, etc.) emit camelCase keys instead of snake_case.
- `IdentifierType.identifier` now serializes as a bare 32-char hex string (instead of dashed UUID format) to match Cascade's REST API requirements.
- `CascadeWrapperBase` constructor was failing with `TypeError` due to `Operations` receiving `logger=` kwarg instead of matching its dataclass field name `_logger=`.
- Write operations (`edit`, `delete`, `copy`, `move`, `publish`, `checkIn`, `siteCopy`, `editAccessRights`, `markMessage`, `deleteMessage`, `editPreference`, `editWorkflowSettings`, `performWorkflowTransition`) now properly parse and surface bare `{"success": true}` responses instead of silently dropping them.

### Added
- `Asset.asset_type` public property getter to read the asset type without accessing the private `_asset_type` attribute.
- `CascadeSuccess` response model and `parse_success` parser for handling success-only responses from write operations.

**Files changed:** `src/cascade_cms/cmstypes.py`, `src/cascade_cms/operations.py`, `src/cascade_cms/wrapper.py`

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

**Files changed:** `pyproject.toml`, `src/cascade_cms/cmstypes.py`, `src/cascade_cms/driver.py`, `src/cascade_cms/operations.py`, `src/cascade_cms/wrapper.py`
