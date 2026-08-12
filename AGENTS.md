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

Use the skill in `skill/cascade-script-writer/`. Start from a template:

```bash
cd skill/cascade-script-writer
python scripts/new_script.py --list
python scripts/new_script.py --template create-bulk --out my_script.py
```

Then validate:

```bash
python skill/cascade-script-writer/scripts/validate_script.py my_script.py
```

**A generated script is complete only when `validate_script.py` exits 0.** Fix
what it reports and re-run; cap at 3 attempts, then surface the output verbatim
rather than looping.

Two rules the validator enforces and scripts must never break:

- `CascadeWrapperBase` is the only entry point — no `cascade_cms.driver`, no
  manual asyncio event loop, no hand-built `aiohttp.ClientSession`.
- `Asset` fields are written by attribute (`asset.keywords = value`), never by
  subscript (`asset["keywords"] = value` raises `TypeError`).

## Rebuilding the skill

```bash
python skill/build_skill.py
```

Syncs `src/cascade_cms/` into the skill bundle, rewrites the manifest,
validates all templates, and zips. Never copy the snapshot by hand — a stale
bundle makes the validator reject correct scripts.
