# Changelog

All notable changes to this project will be documented in this file.

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
