"""`script_log`: lets a script write `[NOTE]` lines into the active run's logfile.

    from cascade_cms.utils import script_log

    with CascadeWrapperBase(env) as cascade:
        script_log.note("will delete page 1a2b... /news/old-1")

`script_log` is a stateless singleton. It holds no run of its own: it looks
the active run up in a `ContextVar` that `CascadeWrapperBase` sets when its
`with` block opens and resets when it exits, so two wrappers open on two
threads (e.g. the MCP) each receive only their own notes.
"""

import warnings
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .redaction import mask_token

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

        Call it inside the `with CascadeWrapperBase(...)` block. Thread-pool
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
                "script_log.note() called with no active CascadeWrapperBase run; "
                f"the note was not written: {text!r}",
                RuntimeWarning,
                stacklevel=2,
            )
            return
        if run.api_key:
            text = text.replace(run.api_key, mask_token(run.api_key))
        run.logger.log_note(" ".join(text.split("\n")).replace("\r", " "))


script_log = ScriptLog()
