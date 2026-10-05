"""Extensions: script logging and identifier coercion.

Script logging: `script_log` lets a script write `[NOTE]` lines into the active
run's logfile:

    from cascade_cms.utils import script_log

    with Cascade(env) as cascade:
        script_log.note("will delete page 1a2b... /news/old-1")

`script_log` is a stateless singleton. It holds no run of its own: it looks
the active run up in a `ContextVar` that `Cascade` sets when its
`with` block opens and resets when it exits, so two wrappers open on two
threads (e.g. the MCP) each receive only their own notes.

Identifier coercion: `to_identifier` and `to_identifiers` coerce raw Cascade
``{id, type, path}`` dicts into `IdentifierType`. Pure data-shape helpers:
no I/O, no requests.
"""

import warnings
from collections.abc import Iterable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from cascade_cms.cmstypes import IdentifierType

if TYPE_CHECKING:
    from .operation_logger import OperationLogger


@dataclass(frozen=True)
class ActiveRun:
    """What the facade needs to write a note for one wrapper's run."""

    logger: "OperationLogger"
    api_key: str = ""


_active_run: ContextVar[ActiveRun | None] = ContextVar("cascade_active_run", default=None)


def activate(run: ActiveRun) -> Token[ActiveRun | None]:
    """Make `run` the active run in the current context; keep the token."""
    return _active_run.set(run)


def deactivate(token: Token[ActiveRun | None]) -> None:
    """Restore whatever was active before the matching `activate()`."""
    _active_run.reset(token)


class ScriptLog:
    """Facade for script-authored log lines. Use the `script_log` instance."""

    def note(self, text: str) -> None:
        """Write `[NOTE]: <text>` to the active run's logfile.

        Call it inside the `with Cascade(...)` block. Thread-pool
        and async callbacks may call it directly (they run in the run's
        context). **Process-pool callbacks cannot**: they run in another
        process with no active run, so return values from the callback and
        note them in the main script.

        Newlines are collapsed to spaces so every note stays one greppable
        line, and any occurrence of the run's API key is replaced by its
        masked form. With no active run (before or after the `with` block)
        the note is dropped and a `RuntimeWarning` is emitted.
        """
        run = _active_run.get()
        if run is None:
            warnings.warn(
                "script_log.note() called with no active Cascade run; "
                f"the note was not written: {text!r}",
                RuntimeWarning,
                stacklevel=2,
            )
            return
        if run.api_key:
            text = text.replace(run.api_key, mask_token(run.api_key))
        run.logger.log_note(" ".join(text.split("\n")).replace("\r", " "))


script_log = ScriptLog()


_MAX_REASON = 120
_VALUE_ERROR_PREFIX = "Value error, "


def _reason(error: ValidationError) -> str:
    """Summarise a validation error as field names and reasons only.

    Input values are never included, and each reason is kept short.
    """
    parts: list[str] = []
    for detail in error.errors(include_input=False):
        loc = detail["loc"]
        name = ".".join(str(p) for p in loc) or "entry"
        if loc and loc[0] in ("type", "asset_type"):
            msg = "not a known asset type"
        elif loc and loc[0] == "path" and detail["type"] in (
            "model_type",
            "model_attributes_type",
        ):
            msg = "must be an object with 'path' and 'siteName'/'siteId'"
        else:
            msg = detail["msg"].removeprefix(_VALUE_ERROR_PREFIX)
        if len(msg) > _MAX_REASON:
            msg = msg[: _MAX_REASON - 3] + "..."
        parts.append(f"{name}: {msg}")
    return "; ".join(parts)


def to_identifier(raw: Mapping[str, Any]) -> IdentifierType:
    """Build an `IdentifierType` from a raw ``{id, type, ...}`` mapping.

    Accepts Cascade's own keys (``id``, ``type``) and the snake_case
    field names (``identifier``, ``asset_type``). Never filters by
    ``recycled``.

    Raises:
        ValueError: if ``raw`` is not a mapping or is malformed. The
            message names fields and reasons, never input values.
    """
    if not isinstance(raw, Mapping):
        raise ValueError(f"expected a mapping, got {type(raw).__name__}")  # noqa: TRY004
    try:
        return IdentifierType(**raw)
    except ValidationError as err:
        raise ValueError(_reason(err)) from err


def to_identifiers(
    raw: Iterable[Mapping[str, Any]] | None,
    *,
    include_recycled: bool = False,
) -> list[IdentifierType]:
    """Build identifiers from raw entries such as a folder's ``children``.

    ``None`` gives ``[]``. Entries flagged ``recycled`` are dropped
    unless ``include_recycled`` is true. Order is preserved.

    Raises:
        ValueError: prefixed ``entry <index>: `` for a malformed entry.
    """
    if raw is None:
        return []
    found: list[IdentifierType] = []
    for index, entry in enumerate(raw):
        try:
            ident = to_identifier(entry)
        except ValueError as err:
            raise ValueError(f"entry {index}: {err}") from err
        if ident.recycled and not include_recycled:
            continue
        found.append(ident)
    return found


def mask_token(token: str) -> str:
    """Return `"****"` plus the last 4 characters of `token`.

    A token of 4 or fewer characters is masked completely (all `*`), so the
    masked form never reveals a short token in full.
    """
    if len(token) <= 4:
        return "*" * len(token)
    return "****" + token[-4:]