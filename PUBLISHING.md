# Version Publishing Guide

## Pre-Release Checklist

1. **Ensure all work is committed**
   ```bash
   git status  # Should show "working tree clean"
   ```

2. **Run full CI suite locally**
   ```bash
   ruff check .        # Lint
   mypy src/           # Type check
   pytest              # Tests
   ```

3. **Verify tests pass on main branch**
   - Push to GitHub and watch CI run, or
   - All checks should pass before tagging

## Release Steps

### 1. Update Version Number
   
   - Edit `pyproject.toml`
   - Change `version = "X.Y.Z"` to the new version (use semantic versioning)
   
   Example: `3.0.1` → `3.0.2` (patch), `3.1.0` (minor), `4.0.0` (major)

### 2. Update CHANGELOG

   - Add a new section at the top with the version and date
   - List all changes (features, fixes, breaking changes)
   - Keep consistent formatting
   
   Example:
   ```markdown
   ## 3.0.2 (2026-08-18)
   
   - Fix RUF012 linting violations in cmstypes.py
   - Simplify nested if statement in operations.py
   - Upgrade ruff to 0.16.3
   ```

### 3. Commit Version Changes

   ```bash
   git add pyproject.toml CHANGELOG.md
   git commit -m "Bump version to 3.0.2"
   ```

### 4. Create Git Tag

   - Tag format: `v{VERSION}` (e.g., `v3.0.2`)
   
   ```bash
   git tag -a v3.0.2 -m "Release version 3.0.2"
   ```
   
   Or lightweight (without annotation):
   ```bash
   git tag v3.0.2
   ```

### 5. Push to Remote

   ```bash
   git push origin master              # Push commits
   git push origin v3.0.2              # Push specific tag
   ```
   
   Or push everything at once:
   ```bash
   git push origin master --tags       # Push commits + all tags
   ```

### 6. Build & Publish to PyPI

   **Build:**
   ```bash
   python -m build
   ```
   
   This creates `dist/cascade_cms_rest-3.0.2-py3-none-any.whl` and `.tar.gz`

   **Publish:**
   ```bash
   twine upload dist/*
   ```
   
   You'll be prompted for PyPI credentials (or use `.pypirc` config)

## Verification

After publishing, verify on PyPI:
```
https://pypi.org/project/cascade-cms-rest/
```

Check version appears and is marked as latest.

## Quick Reference

Typical release flow:

```bash
# 1. Update version in pyproject.toml (e.g., 3.0.1 → 3.0.2)
# 2. Add entry to CHANGELOG.md
# 3. Commit both
git commit -m "Bump version to 3.0.2"

# 4. Create an annotated tag (v-prefix is convention)
git tag -a v3.0.2 -m "Release version 3.0.2"

# 5. Push commits and tags to GitHub
git push origin master --tags

# 6. Build and publish to PyPI
python -m build
twine upload dist/*
```

## Troubleshooting

**Tag already exists:** 
```bash
git tag -d v3.0.2              # Delete local tag
git push origin :refs/tags/v3.0.2  # Delete remote tag
git tag -a v3.0.2 -m "msg"    # Recreate
```

**Need to modify last commit before pushing:**
```bash
# Make changes, then:
git add .
git commit --amend --no-edit   # Or with -m "new message"
```

**Already pushed but need to fix:**
- If only local: use `git reset` or `git commit --amend`
- If already pushed: create a new release version (don't force-push to master)

## Important Notes

- **Never** force-push to `master` or delete published tags (breaks user's package history)
- Use `git log --oneline` to see recent commits before tagging
- Tags are immutable once pushed — treat them like published releases
- For pre-releases, use tags like `v3.0.2-beta1` or `v3.0.2rc1`
