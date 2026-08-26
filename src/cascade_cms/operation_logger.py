# operation_logger.py
"""Owns all console, logfile, and (verbose mode) request/response file output
for the cascade_cms library.

Rendering model (design doc `chain-node-and-logger-design.md` Part 2 / the
pass-2 rework decision log):

- One line per chain: `(uuid_or_path, asset_type) OP1 -> fn_name: Type -> ...`,
  built via `ChainLineBuilder` and produced by `OperationLogger.flush_chain`/
  `flush_chain_error`.
- Lines are built incrementally in memory as a chain executes (append_step()
  per node, reflecting real step-by-step progress — a hang mid-chain could
  still be introspected via render_in_progress()), but are only written to
  the console/logfile once, at the point the chain finishes or stops. Chains
  run concurrently, so a true live in-place redraw of N lines is not
  practical in a scrolling console or an append-only logfile; write-once
  avoids that without losing the incremental-construction property.
- `[METHOD] URL` request/response detail lines and the raw request/response
  JSON files are verbose (debug) mode only, and never inline a payload.
- Two modes, controlled solely by whether `debug_config` is `None`:
    - Normal (debug_config=None): minimal console, simple logfile.
    - Debug (debug_config=dict): quiet console, verbose logfile plus the
      per-request JSON files.
  There is no longer a separate "log operations vs. callbacks vs. responses"
  toggle — `_is_debug` is the single on/off switch for verbose behavior.

Recognized `debug_config` keys:
    log_dir              — directory for the logfile and (verbose mode)
                            request/response JSON files. Default "./logs".
    show_network_headers — verbose mode only: also log request/response
                            HTTP headers.
"""
import itertools
import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Distinguishes loggers built within the same second, which would otherwise
# share one underlying logging.Logger and cross-write into each other's files.
_logger_serial = itertools.count()


def _format_chain_identifier(identifier: Any) -> str:
    """Render the `(uuid_or_path, asset_type)` prefix for a chain's line.

    Adapted from the old logger's `_format_identifier`: same dual
    IdentifierType/Path/dict handling, but the output shape changes, and so
    does the priority — an `IdentifierType` always shows its UUID here (even
    when path info is also available on it), and only a `Path`-only
    reference (no UUID at all) falls back to showing the path string in the
    same position, still paired with the asset type.
    """
    if identifier is None:
        return "NONE, ?"
    if hasattr(identifier, "get_id") and identifier.get_id:
        return f"{identifier.get_id}, {identifier.get_type}"
    if hasattr(identifier, "get_path") and identifier.get_path:
        return f"{identifier.get_path}, {identifier.get_type}"
    if isinstance(identifier, dict):
        path = identifier.get("path")
        asset_type = identifier.get("asset_type", "?")
        if path:
            return f"{path}, {asset_type}"
    return f"{identifier}, ?"


class ChainLineBuilder:
    """Accumulates one chain's pipeline-line segments as its nodes execute.

    Owns only this line's in-progress state (an identifier prefix and an
    ordered list of completed segment strings) and has no dependency on
    operations.py's `Node`/`OperationChain` shape — it's driven purely by
    primitive arguments, so it is usable and testable standalone. What each
    segment's label *is* (a bare operation name, `fn_name: ReturnType`, ...)
    is entirely the caller's decision (see pass-3 wiring); this class only
    joins and aligns whatever labels it's given.
    """

    def __init__(self) -> None:
        self.identifier: str = ""
        self._segments: list[str] = []

    def start(self, identifier: Any) -> None:
        """Resolve and store the `(uuid_or_path, asset_type)` line prefix."""
        self.identifier = _format_chain_identifier(identifier)

    def append_step(self, label: str) -> None:
        """Append one segment — an operation name or a `fn_name: Type` label."""
        self._segments.append(label)

    def render_in_progress(self) -> str:
        """The line as it stands, with a trailing arrow — not yet finished.

        Exposed for introspection of a chain that is still executing (e.g. a
        hang mid-chain); the normal success/error flush paths use
        `render_complete()`/`render_error()` instead, since by the time a
        chain finishes or stops it is no longer "in progress".
        """
        body = " -> ".join(self._segments)
        prefix = f"({self.identifier}) "
        return f"{prefix}{body} -> " if body else f"{prefix}-> "

    def render_complete(self) -> str:
        """The finished (or stopped) line's text, with no trailing arrow."""
        return f"({self.identifier}) {' -> '.join(self._segments)}"

    def render_error(
        self,
        failing_step_index: int,
        message: str,
        file: str,
        line: int,
    ) -> tuple[str, str]:
        """The `v` marker and `!ERROR:` block for a failure at `failing_step_index`.

        `failing_step_index` indexes into the segments already appended via
        `append_step()` — the failing step's own (possibly unresolved) label
        is expected to already be present at that index (e.g. an operation's
        bare name, appended when its request was issued — see pass-3 wiring)
        so the column below aligns under its first character.

        The column is computed by reconstructing the line's literal text up
        to (not including) that label and measuring its length: each " -> "
        separator is 4 characters (both surrounding spaces), not 2 — the
        most likely source of an off-by-one here.

        A multi-line `message` keeps every continuation line at the same
        indentation as the first (no progressive indent); `@{file}:{line}`
        is appended to the last line of the message.
        """
        prefix = f"({self.identifier}) "
        preceding = self._segments[:failing_step_index]
        reconstructed = prefix + " -> ".join(preceding)
        if preceding:
            reconstructed += " -> "
        column = len(reconstructed)
        indent = " " * column

        message_lines = message.splitlines() or [""]
        last_index = len(message_lines) - 1
        error_lines = [
            f"{indent}{'!ERROR: ' if i == 0 else ''}{text}"
            f"{f' @{file}:{line}' if i == last_index else ''}"
            for i, text in enumerate(message_lines)
        ]
        return f"{indent}v", "\n".join(error_lines)


class OperationLogger:
    """Owns all console and logfile output for the cascade_cms library.

    See the module docstring for the two modes and the write-once-per-chain
    rendering decision.
    """

    def __init__(self, server: str, debug_config: dict | None = None):
        self._server = server
        self._config: dict[str, Any] = debug_config if debug_config is not None else {}
        self._is_debug = debug_config is not None
        self._error_count = 0  # cumulative across every batch, reported at exit
        self._processed_count = 0  # cumulative across every batch
        self._start_time: datetime | None = None
        self._script_name: str = ""

        self._file_logger = self._setup_file_logger()
        self._console_logger = self._setup_console_logger()

    @property
    def is_debug(self) -> bool:
        """Whether this logger is in verbose (debug) mode.

        Pass-3 integration seam: callers (operations.py, driver.py) need
        this to decide whether it's worth doing verbose-only work (e.g.
        serializing a payload to reference its file) *before* calling into
        logger methods that already no-op internally outside debug mode —
        this just avoids paying that cost for nothing in normal mode.
        """
        return self._is_debug

    # ------------------------------------------------------------------ #
    # Setup                                                                #
    # ------------------------------------------------------------------ #

    def _log_dir(self) -> Path:
        return Path(self._config.get("log_dir", "./logs"))

    def _setup_file_logger(self) -> logging.Logger:
        log_dir = self._log_dir() if self._is_debug else Path("./logs")
        log_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
        suffix = "debug" if self._is_debug else None
        parts = [self._server, suffix, timestamp] if suffix else [self._server, timestamp]
        filename = "_".join(parts) + ".log"
        log_path = log_dir / filename

        logger = logging.getLogger(f"cascade.file.{timestamp}.{next(_logger_serial)}")
        logger.propagate = False
        handler = logging.FileHandler(log_path)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        return logger

    def _setup_console_logger(self) -> logging.Logger:
        logger = logging.getLogger("cascade.console")
        logger.propagate = False
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        return logger

    # ------------------------------------------------------------------ #
    # Session lifecycle                                                    #
    # ------------------------------------------------------------------ #

    def log_init(self, cascade_url: str, script_name: str):
        self._script_name = script_name
        self._start_time = datetime.now(UTC)
        self._console("[INIT]: Connecting to " + self._server)
        if self._is_debug:
            self._console("[DEBUG]: running in debug mode")
            if self._config.get("show_network_headers"):
                self._write("[INPUTS-CONFIGS]:")
                self._write(f"CASCADE_URL={cascade_url}")

    def log_exit(self):
        elapsed = (
            datetime.now(UTC) - self._start_time
        ).total_seconds() if self._start_time else 0
        self._console(
            f"[DONE]: {self._processed_count} assets processed "
            f"in {elapsed:.1f}s"
        )
        if self._error_count > 0:
            log_filename = self._file_logger.handlers[0].baseFilename \
                if self._file_logger.handlers else "logfile"
            self._console(
                f"[ERRORS]: {self._error_count} failure"
                f"{'s' if self._error_count > 1 else ''} — "
                f"check {Path(log_filename).name}"
            )
        self._console("[EXIT]: Disconnecting from " + self._server)

    # ------------------------------------------------------------------ #
    # Batch framing — brackets one submit_requests() call                 #
    # ------------------------------------------------------------------ #

    def log_batch_start(self) -> None:
        """Bracket the start of one `submit_requests()` batch.

        A script may call `submit_requests()` multiple times in one
        session, each getting its own start/end pair — this is
        deliberately separate from the session-level `log_init`/`log_exit`.
        """
        if self._is_debug:
            self._write(">>>> START REQUEST <<<<")
        else:
            self._console(f"[RUNNING]: {self._script_name}")

    def log_batch_end(self, succeeded: int, total: int) -> None:
        """Close one batch's bracket and report its tally.

        `succeeded`/`total` are supplied by the caller (already known from
        the batch's own results) rather than tracked incrementally here, so
        this is the single place both the per-batch and the running
        session-total (`log_exit`) counts are updated.
        """
        self._processed_count += total
        self._error_count += total - succeeded
        self._console(f"{succeeded}/{total} succeeded")
        if self._is_debug:
            self._write(f"{succeeded}/{total} succeeded")
            self._write(">>>> END REQUEST <<<<")

    # ------------------------------------------------------------------ #
    # Chain line flushing (write-once-per-chain)                          #
    # ------------------------------------------------------------------ #

    def flush_chain(self, builder: ChainLineBuilder) -> None:
        """Write a chain's finished (successful) line to the logfile."""
        self._write(builder.render_complete())

    def flush_chain_error(
        self,
        builder: ChainLineBuilder,
        failing_step_index: int,
        message: str,
        file: str,
        line: int,
    ) -> None:
        """Write a stopped chain's line plus its `v`/`!ERROR:` block.

        Writes the pipeline text (via `render_complete()`, since the
        failing step's own label is already the last appended segment —
        see `ChainLineBuilder.render_error`) followed by the alignment
        block, all in one flush.
        """
        self._write(builder.render_complete())
        v_line, error_block = builder.render_error(failing_step_index, message, file, line)
        self._write(v_line)
        self._write(error_block)

    # ------------------------------------------------------------------ #
    # Request/response detail (verbose mode)                              #
    # ------------------------------------------------------------------ #

    def log_request_detail(
        self, method: str, url: str, payload_ref: str | None = None
    ) -> None:
        """Write `[METHOD] URL`, once per server-touching operation.

        Debug-mode only — normal mode's logfile is just the one pipeline
        line per chain (from `flush_chain`/`flush_chain_error`) plus the
        batch tally; per-request detail is reserved for verbose output.
        No payload is ever inlined here, in any mode.
        """
        if not self._is_debug:
            return
        line = f"[{method}] {url}"
        if payload_ref is not None:
            line += f" | payload: {payload_ref}"
        self._write(line)

    def _write_json_file(self, filename: str, data: Any) -> None:
        log_dir = self._log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / filename).write_text(json.dumps(data, indent=2))

    @staticmethod
    def _parse_json_ish(data: bytes | str | dict) -> Any:
        if isinstance(data, dict):
            return data
        return json.loads(data)

    def write_request_file(self, uuid: str, payload: bytes | dict) -> None:
        """Write `{uuid}_request.json` to the logs folder. Verbose mode only."""
        if not self._is_debug:
            return
        self._write_json_file(f"{uuid}_request.json", self._parse_json_ish(payload))

    def write_response_file(self, uuid: str, response: bytes | dict) -> None:
        """Write `{uuid}_response.json` to the logs folder. Verbose mode only.

        Skipped for a trivial success/fail shape — a dict whose keys are a
        subset of `{"success", "message"}` — since that carries no
        information beyond what the chain line already shows.
        """
        if not self._is_debug:
            return
        parsed = self._parse_json_ish(response)
        if isinstance(parsed, dict) and set(parsed.keys()) <= {"success", "message"}:
            return
        self._write_json_file(f"{uuid}_response.json", parsed)

    def log_network_headers(self, request_headers: dict, response_headers: dict):
        """Write network header info. Verbose mode + show_network_headers only."""
        if not self._is_debug or not self._config.get("show_network_headers", False):
            return
        self._write("[request-headers]:")
        for k, v in request_headers.items():
            self._write(f"  {k}: {v}")
        self._write("[response-headers]:")
        for k, v in response_headers.items():
            self._write(f"  {k}: {v}")

    # ------------------------------------------------------------------ #
    # Error logging (chain-agnostic entry points)                         #
    # ------------------------------------------------------------------ #

    def log_cascade_error(self, message: str, identifier: Any) -> None:
        """Log a CascadeError (API-level failure) outside of chain context.

        Thin wrapper per the design (chain-level failures normally go
        through `flush_chain_error`, which has step-index context this
        doesn't) — kept as a standalone entry point for a request-level
        failure with no enclosing chain line.
        """
        display = _format_chain_identifier(identifier)
        self._console("[ERROR]: CascadeError — check log")
        self._write(f"({display})")
        self._write("v")
        self._write(f"!ERROR: {message}")

    def log_python_error(self, exc: Exception) -> None:
        """Log a Python exception outside of chain context. See `log_cascade_error`."""
        exc_type = type(exc).__name__
        exc_msg = str(exc)
        self._console(f"[ERROR]: {exc_type} — check log")

        tb = traceback.extract_tb(exc.__traceback__)
        frame_info = tb[-1] if tb else None
        file_name = Path(frame_info.filename).name if frame_info else "?"
        line_no = frame_info.lineno if frame_info else 0

        self._write("v")
        self._write(f"!ERROR: {exc_type}: {exc_msg} @{file_name}:{line_no}")

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _write(self, line: str):
        """Write a line to the logfile."""
        self._file_logger.debug(line)

    def _console(self, line: str):
        """Write a line to stdout."""
        self._console_logger.info(line)
