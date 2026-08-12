#!/usr/bin/env python3
"""Build the cascade-script-writer skill package from tracked source.

Syncs src/cascade_cms/ into the skill bundle, records a manifest, validates
every template against the freshly-synced bundle, and only then zips. A
template that fails validation aborts the build, so a bundle can never ship
having drifted from the library it claims to validate against.

    python skill/build_skill.py                  # version from pyproject.toml
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
SKILL_SRC = REPO_ROOT / "skill" / "cascade-script-writer"
BUNDLE_PKG = SKILL_SRC / "cascade_cms"
TEMPLATES = SKILL_SRC / "templates"
VALIDATOR = SKILL_SRC / "scripts" / "validate_script.py"
MANIFEST = BUNDLE_PKG / "_bundle_manifest.json"


def read_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        sys.exit("[BUILD ERROR] Could not find version in pyproject.toml")
    return match.group(1)


def sync_snapshot() -> list[Path]:
    BUNDLE_PKG.mkdir(parents=True, exist_ok=True)
    for stale in BUNDLE_PKG.glob("*.py"):
        stale.unlink()
    copied = []
    for source in sorted(SRC_PKG.glob("*.py")):
        shutil.copy2(source, BUNDLE_PKG / source.name)
        copied.append(BUNDLE_PKG / source.name)
    print(f"[OK] Synced {len(copied)} file(s) from {SRC_PKG.relative_to(REPO_ROOT)}")
    return copied


def write_manifest(version: str, files: list[Path]) -> None:
    MANIFEST.write_text(
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


def validate_templates() -> None:
    templates = sorted(TEMPLATES.glob("*.py"))
    if not templates:
        sys.exit("[BUILD ERROR] No templates found — refusing to ship an empty bundle")

    failures = []
    for template in templates:
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR), str(template)],
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


def build_zip(version: str) -> Path:
    out = REPO_ROOT / "skill" / f"cascade-script-writer-{version.replace('.', '')}.skill"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SKILL_SRC.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            zf.write(path, path.relative_to(SKILL_SRC.parent))
    print(f"[OK] Wrote {out.relative_to(REPO_ROOT)}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="override version from pyproject.toml")
    parser.add_argument("--no-zip", action="store_true", help="sync + validate only")
    args = parser.parse_args()

    version = args.version or read_version()
    print(f"Building cascade-script-writer against cascade-cms-rest {version}\n")

    files = sync_snapshot()
    write_manifest(version, files)
    validate_templates()

    if not args.no_zip:
        build_zip(version)


if __name__ == "__main__":
    main()
