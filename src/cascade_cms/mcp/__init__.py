"""cascade-cms-rest-mcp - a local, read-only MCP server wrapping cascade_cms.

Not imported from `cascade_cms/__init__.py` on purpose: the `mcp` SDK
dependency lives only in this repo's `dev` optional-dependency group, and a
normal `pip install cascade-cms-rest` must stay unaffected by it.
"""
