"""Environment/config wiring for the MCP server, matching CascadeWrapperBase's
existing expectations exactly - no new credential-naming surface.
"""

from __future__ import annotations

import os
from pathlib import Path

from cascade_cms.wrapper import EnvironmentVars

# The two hard requirements. SERVER is cosmetic (log-file naming) and
# defaults, matching every existing skill template's convention.
_REQUIRED_VARS = ("CASCADE_API_KEY", "CASCADE_URL")


def load_environment_variables() -> EnvironmentVars:
    """Read credentials from os.environ, or raise SystemExit naming exactly
    what's missing. Call this at server startup, before the MCP session
    starts - fail fast, not on first tool call.
    """
    missing = [name for name in _REQUIRED_VARS if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            "cascade-cms-rest-mcp: missing required environment variable(s): "
            + ", ".join(missing)
            + '. Set these in your MCP client\'s "env" block, e.g. '
            '{"CASCADE_API_KEY": "...", "CASCADE_URL": "https://your-cascade-host"}.'
        )
    return EnvironmentVars(
        API_KEY=os.environ["CASCADE_API_KEY"],
        CASCADE_URL=os.environ["CASCADE_URL"],
        SERVER=os.environ.get("SERVER", "default"),
    )


def cache_configuration() -> dict[str, object]:
    """`configurationVariables` for CascadeWrapperBase.

    Uses an explicit, stable cache path rather than the library's
    CWD-relative `./cache/cache.sqlite` default, since an MCP client
    (uvx, Claude Desktop, ...) launches this process from an unpredictable
    working directory - a relative path would scatter cache dirs.
    Override with CASCADE_MCP_CACHE_DIR for advanced use / testing.
    """
    cache_dir = Path(
        os.environ.get(
            "CASCADE_MCP_CACHE_DIR", str(Path.home() / ".cache" / "cascade-cms-mcp")
        )
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    return {
        "cache_name": str(cache_dir / "cache.sqlite"),
        "allowed_codes": (200,),
        "allowed_methods": ("GET",),
    }
