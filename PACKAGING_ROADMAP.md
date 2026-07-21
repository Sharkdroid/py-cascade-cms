# Packaging Roadmap: Distributing `cascade_cms` on PyPI

This document lays out the cleanest path from the current repo layout to a publishable PyPI package. Nothing here has been executed — it's a recommendation to work through when the library is ready to ship.

## 1. Target file tree

Move to a `src/`-layout. It forces imports to go through the installed package (not the working directory), which is what actually catches packaging mistakes before a user does.

```
py-cascade-cms/
├── pyproject.toml
├── README.md
├── LICENSE
├── CHANGELOG.md
├── .gitignore
├── src/
│   └── cascade_cms/
│       ├── __init__.py
│       ├── cmstypes.py
│       ├── driver.py
│       ├── operations.py
│       └── wrapper.py
├── tests/
│   ├── conftest.py
│   ├── test_cmstypes.py
│   ├── test_operations.py
│   └── test_driver.py
├── examples/
│   └── read_and_update_asset.py       # test_script.py's pattern, cleaned up
└── .github/
    └── workflows/
        └── ci.yml
```

Changes from today:
- `cascade_cms/` moves under `src/`.
- `test_script.py` moves into `examples/` (it's a usage demo, not a test — see §3) with the hardcoded credentials replaced by environment variable reads.
- `cache/cache.sqlite` stays out of version control (add to `.gitignore` if not already).

## 2. `pyproject.toml`

Recommend **Hatchling** as the build backend — zero-config for a single-package `src/` layout, no `setup.py` needed.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "cascade-cms"
version = "0.1.0"
description = "A typed, async REST client for Hannon Hill Cascade CMS"
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"          # uses `Self`, `TypeAlias`, PEP 604 unions
authors = [{ name = "..." }]
dependencies = [
    "aiohttp-client-cache[sqlite]",
    "python-dotenv",
    "pydantic>=2",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "mypy", "ruff", "build", "twine"]

[project.urls]
Homepage = "https://github.com/<org>/py-cascade-cms"

[tool.hatch.build.targets.wheel]
packages = ["src/cascade_cms"]
```

Note the current code already uses `Self` (PEP 673) and `X | Y` unions, so `requires-python` should be pinned to whatever minimum Python version was actually tested (3.11+ recommended; confirm against the `.venv`'s interpreter version).

## 3. Tests vs. examples

There is currently no `tests/` directory — `test_script.py` is a manual, network-hitting demo script (hardcoded API key/URL), not an automated test. For PyPI-quality packaging:
- Move it to `examples/` and strip the hardcoded credentials in favor of `os.environ` + `python-dotenv`.
- Add real `tests/` using `pytest` + `pytest-asyncio`, mocking `aiohttp.ClientSession` (e.g. via `aioresponses` or a hand-rolled fake session) so tests don't require a live Cascade instance. Priority coverage: `resolve_identifier` (both `IdentifierType` and `Path` branches), `RequestExecutor.fetch` caching behavior, and the `ResponseParser` CascadeError-vs-success branching.

## 4. Versioning

Start with manual versioning in `pyproject.toml` (`version = "0.1.0"`), bump by hand per release. Once the release cadence is established, consider `hatch-vcs` to derive the version from git tags instead of hand-editing — not necessary for a first release.

## 5. README

Currently there is no `README.md`. It should show the fluent pattern already demonstrated in `test_script.py`/`examples/`:
```python
with CascadeWrapperBase(env, config) as cascade:
    cascade.operations.read(identifier)
    results = cascade.submit_requests(Asset)
```
plus a short note on the `IdentifierType | Path` addressing modes now supported.

## 6. CI

A minimal `.github/workflows/ci.yml` running on push/PR: install `.[dev]`, run `ruff check`, `mypy src/`, `pytest`. Keep it to lint + type-check + test — no publish step in this workflow (see §7).

## 7. Build & publish checklist (manual, not automated here)

1. `python -m build` → produces `dist/*.whl` and `dist/*.tar.gz`.
2. `twine check dist/*` → validates metadata/README rendering before upload.
3. Upload to **TestPyPI** first: `twine upload --repository testpypi dist/*`, install in a scratch venv, sanity-check `import cascade_cms`.
4. Only once TestPyPI install is confirmed clean: `twine upload dist/*` to the real index.
5. Tag the release in git (`git tag vX.Y.Z`) matching the published version.

Steps 3-5 involve publishing to a shared, effectively irreversible public index — do these manually and deliberately, not as part of an automated agent run.

## Summary of concrete next actions
1. Add `pyproject.toml` (§2), remove `requirements.txt` once dependencies are captured there.
2. Restructure into `src/cascade_cms/` (§1).
3. Add `tests/` with mocked HTTP (§3); keep `test_script.py`'s pattern alive as `examples/`.
4. Add `README.md`, `LICENSE`, `.gitignore`, CI workflow (§5, §6).
5. Only then proceed to the manual build/publish checklist (§7).
