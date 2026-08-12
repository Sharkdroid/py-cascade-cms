# operation_logger.py
import logging
import sys
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class OperationLogger:
    """
    Owns all console and logfile output for the cascade_cms library.

    Two modes:
      - Normal (debug_config=None): minimal console, simple logfile.
      - Debug  (debug_config=dict): quiet console, verbose nested logfile.

    Depth tracking for nested operation scopes is managed internally via
    a stack. Callers use operation_scope() as a context manager — they
    never touch _stack directly.
    """

    def __init__(self, server: str, debug_config: dict | None = None):
        self._server = server
        self._config: dict[str, Any] = debug_config if debug_config is not None else {}
        self._is_debug = debug_config is not None
        self._stack: list[str] = []          # active operation scope stack
        self._active_callback: str | None = None  # currently executing callback name
        self._error_count = 0
        self._processed_count = 0
        self._total_count = 0
        self._start_time: datetime | None = None
        self._script_name: str = ""

        self._file_logger = self._setup_file_logger()
        self._console_logger = self._setup_console_logger()

    # ------------------------------------------------------------------ #
    # Setup                                                                #
    # ------------------------------------------------------------------ #

    def _setup_file_logger(self) -> logging.Logger:
        log_dir = Path(self._config.get("log_dir", "./logs")) \
            if self._is_debug else Path("./logs")
        log_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
        suffix = "debug" if self._is_debug else None
        parts = [self._server, suffix, timestamp] if suffix else [self._server, timestamp]
        filename = "_".join(parts) + ".log"
        log_path = log_dir / filename

        logger = logging.getLogger(f"cascade.file.{timestamp}")
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
    # Depth / indent helpers                                               #
    # ------------------------------------------------------------------ #

    @property
    def _depth(self) -> int:
        return len(self._stack)

    def _indent(self) -> str:
        """Pipe indent for lines inside an operation block."""
        if self._depth == 0:
            return "| "
        return "|  " * self._depth + "| "

    def _op_prefix(self) -> str:
        """Prefix for the operation header line."""
        if self._depth == 0:
            return "> "
        return "|  " * (self._depth - 1) + "|---> "

    # ------------------------------------------------------------------ #
    # Context manager                                                      #
    # ------------------------------------------------------------------ #

    @contextmanager
    def operation_scope(self, operation_name: str):
        """
        Push operation onto the depth stack for the duration of the block.
        Always pops on exit, even on exception.
        Writes END OF REQUEST marker when returning to root depth (debug only).
        """
        self._stack.append(operation_name)
        try:
            yield
        finally:
            self._stack.pop()
            if self._is_debug and self._depth == 0:
                self._write(">" * 22 + " END OF REQUEST " + "<" * 22 + "\n")

    @contextmanager
    def callback_scope(self, callback_name: str):
        """
        Track the currently executing callback so nested operations can
        annotate themselves with (via <callback_name>).
        """
        self._active_callback = callback_name
        try:
            yield
        finally:
            self._active_callback = None

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

    def log_running(self, script_name: str):
        if not self._is_debug:
            self._console(f"[RUNNING]: {script_name}")

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
    # Progress                                                             #
    # ------------------------------------------------------------------ #

    def set_total(self, total: int):
        """Call before submit_requests() with len(pending_requests)."""
        self._total_count = total
        self._processed_count = 0

    def log_progress(self, failed: bool = False):
        """Call after each individual request completes."""
        self._processed_count += 1
        if failed:
            self._error_count += 1
        failed_str = (
            f" ({self._error_count} failed)" if self._error_count > 0 else ""
        )
        self._console(
            f"Processed: {self._processed_count}/"
            f"{self._total_count}{failed_str}"
        )

    # ------------------------------------------------------------------ #
    # Operation logging                                                    #
    # ------------------------------------------------------------------ #

    def log_operation(
        self,
        name: str,
        url: str,
        payload: Any,
        parser: Any,
        identifier: Any,
    ):
        """
        Write operation header to logfile.
        In debug mode: full block with URL, payload, parser, identifier.
        In normal mode: single [OPERATION]: path/id line.
        """
        display = self._format_identifier(identifier)

        if self._is_debug:
            if not self._config.get("log_operations", True):
                return
            origin = (
                f" (via {self._active_callback})"
                if self._active_callback else ""
            )
            prefix = self._op_prefix()
            pad = self._indent()
            self._write(f"{prefix}{name}{origin}:")
            self._write(f"{pad}[URL]: {url}")
            if self._config.get("show_payload_data", True):
                payload_str = str(payload) if payload is not None else "NONE"
                self._write(f"{pad}[payload]: {payload_str}")
            parser_name = getattr(parser, "__name__", "NONE") if parser else "NONE"
            self._write(f"{pad}[parser]: {parser_name}")
            self._write(f"{pad}[identifier]: {display}")
        else:
            self._write(f"[{name}]: {display}")

    def log_response(self, raw: bytes):
        """Write response body to logfile. Debug mode only."""
        if not self._is_debug or not self._config.get("log_responses", True):
            return
        pad = self._indent()
        limit = self._config.get("response_line_limit", 8)
        divider = "=" * 25
        self._write(f"{pad}{divider} (RESPONSE) {divider}")
        lines = raw.decode(errors="replace").splitlines()
        if limit == -1 or len(lines) <= limit:
            for line in lines:
                self._write(f"{pad}{line}")
        else:
            for line in lines[:limit]:
                self._write(f"{pad}{line}")
            self._write(f"{pad}... (truncated at {limit} lines)")

    def log_callbacks(self, callbacks: list):
        """Write callback chain line. Debug mode only."""
        if not self._is_debug or not self._config.get("log_callbacks", True):
            return
        pad = self._indent()
        chain = " >> ".join(
            getattr(cb, "__name__", repr(cb)) for cb in callbacks
        )
        self._write(f"{pad}[callbacks]: {chain}")

    def log_network_headers(self, request_headers: dict, response_headers: dict):
        """Write network header info. Debug mode + show_network_headers only."""
        if not self._is_debug or not self._config.get("show_network_headers", False):
            return
        pad = self._indent()
        self._write(f"{pad}[request-headers]:")
        for k, v in request_headers.items():
            self._write(f"{pad}  {k}: {v}")
        self._write(f"{pad}[response-headers]:")
        for k, v in response_headers.items():
            self._write(f"{pad}  {k}: {v}")

    # ------------------------------------------------------------------ #
    # Error logging                                                        #
    # ------------------------------------------------------------------ #

    def log_cascade_error(self, message: str, identifier: Any):
        """
        Log a CascadeError (API-level failure, e.g. asset not found).
        Console: [ERROR]: ErrorType — check log
        Logfile: [ERROR]: path + structured block
        """
        display = self._format_identifier(identifier)
        self._console("[ERROR]: CascadeError — check log")

        if self._is_debug:
            pad = self._indent()
            self._write(f"{pad}")
            self._write(f"*!! CascadeError: {message}")
            self._write(f"  asset: {display}")
            self._write("!!")
        else:
            self._write(f"[ERROR]: {display}")
            self._write("  Error Type: CascadeError")
            self._write(f"  Error Message: {message}")

    def log_python_error(self, exc: Exception):
        """
        Log a Python exception.

        Normal mode: type + message only (no traceback, no variables).
        Debug mode:  type + message + origin file + function + line +
                     local variable snapshot from the failing frame.
                     Variable snapshot is controlled by show_error_variables.

        Console (both modes): error type + 'check log for details'
        """
        exc_type = type(exc).__name__
        exc_msg = str(exc)
        self._console(f"[ERROR]: {exc_type} — check log")

        if self._is_debug:
            tb = traceback.extract_tb(exc.__traceback__)
            frame_info = tb[-1] if tb else None
            frame_locals = (
                exc.__traceback__.tb_frame.f_locals
                if exc.__traceback__ else {}
            )
            pad = self._indent()
            self._write(f"{pad}")
            self._write(f"*!! {exc_type}: {exc_msg}")
            if frame_info:
                self._write(f"  origin:   {Path(frame_info.filename).name}")
                self._write(f"  function: {frame_info.name}")
                self._write(f"  line {frame_info.lineno}: {frame_info.line}")
            if self._config.get("show_error_variables", True) and frame_locals:
                self._write("  variables:")
                for k, v in frame_locals.items():
                    self._write(
                        f"    {k:<10} = {v!r}  ({type(v).__name__})"
                    )
            self._write("!!")
        else:
            self._write("[ERROR]:")
            self._write(f"  Error Type: {exc_type}")
            self._write(f"  Error Message: {exc_msg}")

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _format_identifier(self, identifier: Any) -> str:
        """
        Resolve an identifier to a human-readable string for log output.

        Priority:
          1. Path string if available (e.g. mySite/blog/post-1)
          2. ID + asset type as fallback (e.g. a3f9bc... (folder))
          3. 'NONE' if identifier is None
        """
        if identifier is None:
            return "NONE"
        # IdentifierType instance
        if hasattr(identifier, "get_path") and identifier.get_path:
            site = identifier.get_sitename or ""
            path = identifier.get_path or ""
            return f"{site}/{path}".strip("/")
        if hasattr(identifier, "get_id") and identifier.get_id:
            return f"{identifier.get_id[:8]}... ({identifier.get_type})"
        # Path dict
        if isinstance(identifier, dict):
            site = identifier.get("siteName", "")
            path = identifier.get("path", "")
            if path:
                return f"{site}/{path}".strip("/")
        return str(identifier)

    def _write(self, line: str):
        """Write a line to the logfile."""
        self._file_logger.debug(line)

    def _console(self, line: str):
        """Write a line to stdout."""
        self._console_logger.info(line)
