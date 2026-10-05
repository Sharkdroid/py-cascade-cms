# AGENTS.md

`cascade-cms-rest` — a typed, async REST client for Hannon Hill Cascade CMS 8.
Library source is `src/cascade_cms/`.


## Repo commands

```bash
pip install -e ".[dev]"
pytest
ruff check .
mypy src/
```

CI runs `ruff check .`, `mypy src/`, and `pytest`. Run all three before
considering a change to the library done.

## Writing scripts that use this library

Script-writing skills (`cascade-script-writer`/`cascade-script-writer-lite`)
and the MCP server both moved to their own repo, `cascade-cms-tools`
(sibling to this one), so this library repo stays dependency-light and
focused on `src/cascade_cms/`. See that repo's `AGENTS.md` for the
skill-authoring workflow and how to rebuild release artifacts — both depend
on the *published* `cascade-cms-rest` package, not a local copy, so nothing
here needs to stay in sync with them beyond tagging a release.

## Logging and console output

- `Cascade(environmentVariables, debug=None, *, exit_on_failure=True,
  log_dir=None)` — there is no response cache and no `configurationVariables`.
- Status lines (`[INIT]`, `[LOG]: <path>`, `[DONE]`, the tally, `[EXIT]`, console `[ERROR]`)
  go to **stderr**; stdout belongs to the caller's script.
- The logfile records the tally line and, last, `[EXIT-CODE]: <outcome>` (see the README's
  Logging section for the outcomes). Agents verify a run by reading the logfile at the
  `[LOG]` path, not by capturing console output.
- `log_dir` (explicit parameter) beats a debug config's `log_dir`; default `./logs`. Log
  filenames are `{SERVER}[_debug]_{timestamp}_{n}.log`.
- Successful writes log `[RESULT]: ...` automatically; scripts log `[NOTE]: <text>` with
  `from cascade_cms.utils import script_log` then `script_log.note(text)` inside the `with`
  block (formats in the README). Process-pool callbacks return values and the main script notes
  them.
- Package layout: `cascade_cms/utils/` holds `operation_logger.py`, `redaction.py`,
  `script_notes.py` and `identifiers.py` (`to_identifier`/`to_identifiers`, pure, no I/O).
  `cascade_cms.operation_logger` and `cascade_cms.redaction` no longer exist (no shim); `from cascade_cms import OperationLogger` still works.
- The API bearer token is masked (`cascade_cms.utils.redaction.mask_token`) wherever it could surface.

## Cascade API quirks

- **Page regions are read-only through the API.** Edits to a page's `pageRegions` sent via
  `edit()` are ignored by Cascade. Regions are changed on the `template` asset (`pageRegions`);
  page configurations on the `pageConfigurationSet` asset (`pageConfiguration`). `PageRegion`
  and `PageConfiguration` are read-only snapshots and raise `ReadOnlyPageConfigError` on any
  assignment.
- **Do not trust Cascade's response fields blindly.** With `blockId`/`blockPath` and
  `formatId`/`formatPath` removed from a region, Cascade still returned `noBlock: false` and
  `noFormat: false`. Derive "has a block/format" from the ids/paths, not the flags, and verify
  surprising state against the live server.

## Releasing to PyPI

Pushing a commit to `master` does **not** publish anything. The `Release` workflow
(`.github/workflows/release.yml`) runs only when a tag matching `v*.*.*` is pushed
(e.g. `v3.7.0`; a bare `3.7.0` does not match and triggers nothing). It builds the
sdist/wheel and publishes to PyPI (the `publish` job uses the `pypi` environment).
To release: commit the version bump and CHANGELOG, then
`git tag -a vX.Y.Z -m "Release X.Y.Z"` and `git push origin vX.Y.Z`. See `PUBLISHING.md`.
Downstream consumers (`cascade-cms-tools`) depend on the *published* package, so they
cannot pick up a change until the tag has been pushed and PyPI shows the version.

## Maintaining the CHANGELOG

Each section in `CHANGELOG.md` should include a note at the bottom listing
which files were touched. For example:

```markdown
### Fixed
- Fixed async callback execution order (affects callback chains)
- Fixed nested payload aliasing issue

**Files changed:** `src/cascade_cms/operations.py`, `src/cascade_cms/cmstypes.py`
```

This helps readers quickly understand the scope of changes and identify which
parts of the library were affected.
