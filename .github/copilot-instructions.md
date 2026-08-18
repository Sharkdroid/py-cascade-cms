# Copilot instructions

This repo is `cascade-cms-rest`, an async REST client for Hannon Hill Cascade
CMS 8. Library source: `src/cascade_cms/`.

## Writing a script that uses the library

For a single-operation script, follow
`skill/cascade-script-writer-lite/SKILL.md` — 6 templates, one reference file,
and no reason to open the library source. For multi-operation pipelines or
callback patterns, follow `skill/cascade-script-writer/SKILL.md`:

1. Pick a starting template from `skill/cascade-script-writer/templates/INDEX.md`.
2. Look up exact field names and aliases in
   `skill/cascade-script-writer/references/operations_schema.json`.
3. Validate before handing the script over:

```bash
python skill/cascade-script-writer/scripts/validate_script.py <script.py>
```

**The script is done only when that command exits 0.** Fix what it reports and
re-run, up to 3 attempts, then show the output verbatim.

Non-negotiables: `CascadeWrapperBase` is the only entry point (never
`cascade_cms.driver`, never a manual event loop), and `Asset` fields are set by
attribute assignment, never subscript.

## Working on the library itself

`ruff check .`, `mypy src/`, and `pytest` must all pass. After changing
`src/cascade_cms/`, rebuild both skill bundles with `python skill/build_skill.py`.
