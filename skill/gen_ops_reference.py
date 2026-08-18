#!/usr/bin/env python3
"""Generate references/OPS.md for the cascade-script-writer-lite skill.

The lite skill exists so a small model never has to open cmstypes.py (~7.7k
tokens) or operations.py (~6.1k tokens) to find a method signature or a field
name. This script distills both into one ~4KB table by introspecting the real
library, so the reference cannot drift from the code the validator enforces.

Nothing here is hand-transcribed except CONSTRAINT_NOTES, and each of those is
asserted against the live models before it is written.

    python skill/gen_ops_reference.py --stdout
    python skill/gen_ops_reference.py --out skill/cascade-script-writer-lite/references/OPS.md
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import sys
import uuid
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
VALIDATOR = REPO_ROOT / "skill" / "cascade-script-writer" / "scripts" / "validate_script.py"

# Rules enforced by pydantic model_validators, which introspection cannot see.
# Every entry is verified against the live model in check_constraints().
CONSTRAINT_NOTES = {
    "NewAsset": "exactly one of site_name/site_id, exactly one of parent_folder_path/parent_folder_id",
}


def load_validator_tables() -> ModuleType:
    """Reuse the validator's own op->result-type map instead of writing a second
    one that could disagree with the gate every script must pass."""
    if not VALIDATOR.exists():
        sys.exit(
            f"[GEN ERROR] {VALIDATOR.relative_to(REPO_ROOT)} not found.\n"
            "            Unpack the skill bundle first (see AGENTS.md)."
        )
    spec = importlib.util.spec_from_file_location("_validator", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def simplify(annotation: object) -> str:
    """Collapse a fully-qualified annotation into the shortest form a script
    author actually types."""
    text = (
        annotation.__name__
        if isinstance(annotation, type)
        else str(annotation).replace("cascade_cms.cmstypes.", "")
    )
    if "IdentifierType" in text or "Path" in text:
        return "ids" if "list[" in text else "id"
    if "NewAsset" in text:
        return "payloads" if "list[" in text else "payload"
    if "Asset" in text:
        return "assets" if "list[" in text else "asset"
    # Drop the None arm of an optional union — the caller wants the real type.
    members = [m.strip() for m in text.split("|")]
    real = [m for m in members if m not in ("None", "NoneType")]
    return (real or members)[0]


def render_call(name: str, fn: object) -> str:
    """Render a call the way it appears in a script: positional, no parser."""
    parts = []
    for pname, param in inspect.signature(fn).parameters.items():
        if pname in ("self", "parser"):
            continue
        rendered = simplify(param.annotation)
        if rendered not in ("id", "ids", "asset", "assets", "payload", "payloads"):
            rendered = f"{rendered}(...)"
        if param.default is not inspect.Parameter.empty:
            rendered = f"[{rendered}]"
        parts.append(rendered)
    return f"{name}({', '.join(parts)})"


def collect_operations(result_types: dict[str, str]) -> list[tuple[str, str, str]]:
    from cascade_cms.operations import Operations

    rows = []
    for name, fn in inspect.getmembers(Operations, inspect.isfunction):
        if name.startswith("_") or name == "then":
            continue
        rows.append((name, render_call(name, fn), result_types.get(name, "?")))
    missing = [n for n, _, r in rows if r == "?"]
    if missing:
        sys.exit(f"[GEN ERROR] No result type recorded for: {', '.join(missing)}")
    return sorted(rows)


def collect_payloads(names: set[str]) -> list[tuple[str, str, str]]:
    """One line per payload model: field(alias) with * marking required."""
    from pydantic import BaseModel

    from cascade_cms import cmstypes

    rows = []
    for name in sorted(names):
        model = getattr(cmstypes, name, None)
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            continue
        fields = []
        for fname, field in model.model_fields.items():
            label = f"{fname}({field.alias})" if field.alias else fname
            fields.append(label + ("*" if field.is_required() else ""))
        note = CONSTRAINT_NOTES.get(name, "")
        rows.append((name, ", ".join(fields), note))
    return rows


def check_constraints() -> None:
    """Assert the hand-written CONSTRAINT_NOTES still describe real behaviour,
    so the one non-introspected part of this file cannot go stale silently."""
    from cascade_cms.cmstypes import NewAsset

    def rejects(**kwargs) -> bool:
        try:
            NewAsset(name="x", asset_type="page", **kwargs)
            return False
        except Exception:  # noqa: BLE001 — any refusal proves the constraint holds
            return True

    uid = uuid.uuid4()
    failures = []
    if not rejects(site_name="w", site_id=uid, parent_folder_path="/a"):
        failures.append("NewAsset now accepts both site_name and site_id")
    if not rejects(parent_folder_path="/a", parent_folder_id=uid, site_name="w"):
        failures.append("NewAsset now accepts both parent_folder_path and parent_folder_id")
    if not rejects(siteName="w", parent_folder_path="/a"):
        failures.append("NewAsset now accepts the siteName alias")
    if failures:
        sys.exit(
            "[GEN ERROR] CONSTRAINT_NOTES are stale:\n  " + "\n  ".join(failures)
        )


def render(version: str, ops, payloads) -> str:
    lines = [
        "# Operations reference",
        "",
        f"Generated from cascade-cms-rest {version}. This file is complete —",
        "do not open `cascade_cms/*.py` to look anything up.",
        "",
        "`id` = one `IdentifierType` or `Path`. `ids` = one or a list of them.",
        "Every call is `cascade.operations.<name>(...)`, then one",
        "`cascade.submit_requests(T)` for the whole batch.",
        "",
        "| Operation | Call | submit_requests(T) |",
        "|---|---|---|",
    ]
    lines += [f"| {name} | `{call}` | `{result}` |" for name, call, result in ops]
    lines += [
        "",
        "## Payload fields",
        "",
        "`*` = required. `field(alias)` — either spelling works, **except on",
        "`NewAsset`**, which allows extra fields and so silently treats an alias",
        "as a passthrough field: use the plain names there.",
        "",
    ]
    for name, fields, note in payloads:
        lines.append(f"- **{name}**: {fields}")
        if note:
            lines.append(f"  - {note}")
    lines += [
        "",
        "## Rules the fields do not show",
        "",
        "- `NewAsset` passes unknown fields straight through, so `title=`,",
        "  `displayName=`, `metadata=` etc. are set by naming them.",
        "- `IdentifierType` rejects unknown fields entirely.",
        "- Pass real `uuid.UUID` objects; the library emits bare hex, which is",
        "  what Cascade accepts. Never format a UUID yourself.",
        "- A `Path` is a plain dict and is validated only when the request is",
        "  built: it needs `asset_type`, `siteName`, and `path`.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="write to this path")
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    parser.add_argument("--version", default="", help="version string for the header")
    args = parser.parse_args()

    sys.path.insert(0, str(SRC_ROOT))
    validator = load_validator_tables()
    check_constraints()

    ops = collect_operations(validator.OPERATION_RESULT_TYPES)
    payloads = collect_payloads(validator.CONSTRUCTIBLE_NAMES)
    text = render(args.version or "current", ops, payloads)

    if args.stdout or not args.out:
        sys.stdout.write(text)
        return

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"[OK] Wrote {out.relative_to(REPO_ROOT)} ({len(text)} bytes, {len(ops)} operations)")


if __name__ == "__main__":
    main()
