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
