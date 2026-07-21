# Changelog

All notable changes to this project will be documented in this file.

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
