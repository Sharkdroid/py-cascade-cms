#!/usr/bin/env python3
"""Build the cascade-script-writer skill packages from tracked source.

Two bundles are produced from one source of truth:

  full  cascade-script-writer       — 21 templates, full schema, for frontier models
  lite  cascade-script-writer-lite  — 6 templates, generated OPS.md, for fast models

Both sync src/cascade_cms/ into the bundle, record a manifest, validate every
template against the freshly-synced bundle, and only then zip. A template that
fails validation aborts the build, so a bundle can never ship having drifted
from the library it claims to validate against. The lite bundle additionally
regenerates references/OPS.md and is held to a size budget — that skill exists
to be small, so growth is a build failure, not a judgement call.

    python skill/build_skill.py                  # both, version from pyproject.toml
    python skill/build_skill.py --target lite
    python skill/build_skill.py --version 2.0.3
    python skill/build_skill.py --no-zip         # sync + validate only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PKG = REPO_ROOT / "src" / "cascade_cms"
SKILL_DIR = REPO_ROOT / "skill"
FULL_SRC = SKILL_DIR / "cascade-script-writer"
LITE_SRC = SKILL_DIR / "cascade-script-writer-lite"
GEN_OPS = SKILL_DIR / "gen_ops_reference.py"

# The lite skill ships a subset of the full skill's templates. Anything not
# listed here is a pattern a fast model should not be attempting unaided.
LITE_TEMPLATES = (
    "read-identifiers",
    "read-iterate",
    "create-bulk",
    "edit-in-place",
    "workflow-orchestration",
    "callback-none",
)

# Shared machinery, copied from the full skill so there is one copy to maintain.
SHARED_SCRIPTS = ("validate_script.py", "new_script.py")

# Read budgets for the lite skill, in bytes. A fast model reads SKILL.md, OPS.md,
# and one template — roughly 3.5k tokens. These caps keep it that way.
LITE_BUDGETS = {
    Path("SKILL.md"): 5_000,
    Path("references/OPS.md"): 6_000,
    Path("references/ASSET.md"): 2_500,
}


def read_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        sys.exit("[BUILD ERROR] Could not find version in pyproject.toml")
    return match.group(1)


def sync_snapshot(skill_src: Path) -> list[Path]:
    bundle_pkg = skill_src / "cascade_cms"
    bundle_pkg.mkdir(parents=True, exist_ok=True)
    for stale in bundle_pkg.glob("*.py"):
        stale.unlink()
    copied = []
    for source in sorted(SRC_PKG.glob("*.py")):
        shutil.copy2(source, bundle_pkg / source.name)
        copied.append(bundle_pkg / source.name)
    print(f"[OK] Synced {len(copied)} file(s) from {SRC_PKG.relative_to(REPO_ROOT)}")
    return copied


def write_manifest(skill_src: Path, version: str, files: list[Path]) -> None:
    manifest = skill_src / "cascade_cms" / "_bundle_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "library_version": version,
                "source": "src/cascade_cms",
                "files": {
                    f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files
                },
            },
            indent=2,
        )
        + "\n"
    )
    print(f"[OK] Wrote manifest for cascade-cms-rest {version}")


def sync_lite_sources(version: str) -> None:
    """Mirror the shared parts of the full skill into the lite skill. Only
    SKILL.md and references/ASSET.md are hand-written there; everything else is
    a copy or generated, so the two skills cannot disagree about the library."""
    if not FULL_SRC.exists():
        sys.exit(
            f"[BUILD ERROR] {FULL_SRC.relative_to(REPO_ROOT)} is missing — the lite\n"
            "              skill copies its templates and scripts. Unpack the full\n"
            "              bundle first (see AGENTS.md)."
        )

    scripts_dir = LITE_SRC / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in SHARED_SCRIPTS:
        shutil.copy2(FULL_SRC / "scripts" / name, scripts_dir / name)

    templates_dir = LITE_SRC / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    for stale in templates_dir.glob("*.py"):
        stale.unlink()
    for name in LITE_TEMPLATES:
        source = FULL_SRC / "templates" / f"{name}.py"
        if not source.exists():
            sys.exit(f"[BUILD ERROR] Lite template '{name}' not found in the full skill")
        shutil.copy2(source, templates_dir / f"{name}.py")
    print(f"[OK] Copied {len(SHARED_SCRIPTS)} script(s) and {len(LITE_TEMPLATES)} template(s)")

    proc = subprocess.run(
        [
            sys.executable,
            str(GEN_OPS),
            "--version",
            version,
            "--out",
            str(LITE_SRC / "references" / "OPS.md"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(proc.stdout + proc.stderr)
        sys.exit("[BUILD ABORTED] Could not generate references/OPS.md")
    print(proc.stdout.strip())


def check_budgets() -> None:
    """The lite skill's whole value is its read cost. Enforce it."""
    problems = []
    for relative, limit in LITE_BUDGETS.items():
        path = LITE_SRC / relative
        if not path.exists():
            problems.append(f"{relative} is missing")
            continue
        size = path.stat().st_size
        if size > limit:
            problems.append(f"{relative} is {size} bytes, over its {limit}-byte budget")

    for stray in (LITE_SRC / "references").glob("*.json"):
        problems.append(f"references/{stray.name} — JSON reference files belong in the full skill only")

    if problems:
        print("\n[BUILD ABORTED] lite skill exceeds its read budget:\n")
        for problem in problems:
            print(f"  - {problem}")
        sys.exit(1)
    print("[OK] Lite skill within read budget")


def validate_templates(skill_src: Path) -> None:
    templates = sorted((skill_src / "templates").glob("*.py"))
    validator = skill_src / "scripts" / "validate_script.py"
    if not templates:
        sys.exit("[BUILD ERROR] No templates found — refusing to ship an empty bundle")

    failures = []
    for template in templates:
        proc = subprocess.run(
            [sys.executable, str(validator), str(template)],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            print(f"[OK] {template.name}")
        else:
            failures.append((template.name, proc.stdout + proc.stderr))
            print(f"[FAIL] {template.name}")

    if failures:
        print(f"\n[BUILD ABORTED] {len(failures)} template(s) failed validation:\n")
        for name, output in failures:
            print(f"--- {name} ---")
            print(output)
        sys.exit(1)
    print(f"\n[OK] All {len(templates)} templates passed validation")


def build_zip(skill_src: Path, version: str) -> Path:
    out = SKILL_DIR / f"{skill_src.name}-{version.replace('.', '')}.skill"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(skill_src.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            zf.write(path, path.relative_to(skill_src.parent))
    print(f"[OK] Wrote {out.relative_to(REPO_ROOT)}")
    return out


def build(target: str, version: str, zip_it: bool) -> None:
    skill_src = FULL_SRC if target == "full" else LITE_SRC
    print(f"\n=== {skill_src.name} (cascade-cms-rest {version}) ===\n")

    if target == "lite":
        sync_lite_sources(version)

    files = sync_snapshot(skill_src)
    write_manifest(skill_src, version, files)

    if target == "lite":
        check_budgets()

    validate_templates(skill_src)

    if zip_it:
        build_zip(skill_src, version)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="override version from pyproject.toml")
    parser.add_argument("--no-zip", action="store_true", help="sync + validate only")
    parser.add_argument(
        "--target",
        choices=("full", "lite", "all"),
        default="all",
        help="which bundle to build (default: all)",
    )
    args = parser.parse_args()

    version = args.version or read_version()
    targets = ("full", "lite") if args.target == "all" else (args.target,)

    for target in targets:
        build(target, version, zip_it=not args.no_zip)


if __name__ == "__main__":
    main()
