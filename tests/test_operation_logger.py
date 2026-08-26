"""Tests for the pass-2 `operation_logger.py` rewrite.

Deliberately standalone: nothing here imports or depends on operations.py's
Node/OperationChain shape, matching the design's requirement that the logger
be usable and testable driven purely by primitive arguments (uuid, asset
type, step label, return type, error object, ...).
"""
import io
import json

from cascade_cms.cmstypes import IdentifierType
from cascade_cms.cmstypes import Path as CascadePath
from cascade_cms.operation_logger import ChainLineBuilder, OperationLogger

ID_ONE = "8b320f55ac1001062545a6d2562cee4b"


def _logged_lines(logger: OperationLogger) -> list[str]:
    """Read back whatever a logger wrote to its logfile."""
    handler = logger._file_logger.handlers[0]
    handler.flush()
    with open(handler.baseFilename) as fh:
        return fh.read().splitlines()


# ============================================================================
# ChainLineBuilder: joined output
# ============================================================================

class TestChainLineBuilderJoinedOutput:
    def test_two_step_chain(self):
        builder = ChainLineBuilder()
        builder.start(IdentifierType(id=ID_ONE, type="page"))
        builder.append_step("READ")
        builder.append_step("CascadeSuccess")

        assert builder.render_complete() == f"({ID_ONE}, page) READ -> CascadeSuccess"

    def test_three_step_chain(self):
        builder = ChainLineBuilder()
        builder.start(IdentifierType(id=ID_ONE, type="page"))
        builder.append_step("READ")
        builder.append_step("change_displayName: Asset")
        builder.append_step("EDIT")

        assert builder.render_complete() == (
            f"({ID_ONE}, page) READ -> change_displayName: Asset -> EDIT"
        )

    def test_four_step_chain(self):
        builder = ChainLineBuilder()
        builder.start(IdentifierType(id=ID_ONE, type="page"))
        builder.append_step("READ")
        builder.append_step("change_displayName: Asset")
        builder.append_step("EDIT")
        builder.append_step("CascadeSuccess")

        assert builder.render_complete() == (
            f"({ID_ONE}, page) READ -> change_displayName: Asset -> EDIT -> CascadeSuccess"
        )

    def test_worked_example_folder_copy_scenario(self):
        """A read -> transform -> edit -> result pipeline, end to end."""
        builder = ChainLineBuilder()
        path = CascadePath(
            path="student-newsletter-dev/2022-older",
            siteName="student-newsletter-dev",
            asset_type="page",
        )
        builder.start(path)
        builder.append_step("READ")
        builder.append_step("change_displayname: Asset")
        builder.append_step("EDIT")
        builder.append_step("CascadeSuccess")

        assert builder.render_complete() == (
            "(student-newsletter-dev/2022-older, page) READ -> "
            "change_displayname: Asset -> EDIT -> CascadeSuccess"
        )

    def test_identifier_type_prefers_uuid_over_path(self):
        """An IdentifierType shows its UUID even when path info is also present."""
        identifier = IdentifierType(
            id=ID_ONE,
            type="page",
            path={"path": "mysite/blog/post-1", "siteName": "mysite"},
        )
        builder = ChainLineBuilder()
        builder.start(identifier)

        assert builder.identifier == f"{ID_ONE}, page"

    def test_path_only_shows_path_string(self):
        path = CascadePath(path="/about", siteName="mysite", asset_type="page")
        builder = ChainLineBuilder()
        builder.start(path)

        assert builder.identifier == "/about, page"


# ============================================================================
# ChainLineBuilder: in-progress rendering
# ============================================================================

class TestChainLineBuilderInProgress:
    def test_trailing_arrow_with_one_step(self):
        builder = ChainLineBuilder()
        builder.start(IdentifierType(id=ID_ONE, type="page"))
        builder.append_step("READ")

        assert builder.render_in_progress() == f"({ID_ONE}, page) READ -> "

    def test_trailing_arrow_with_no_steps_yet(self):
        builder = ChainLineBuilder()
        builder.start(IdentifierType(id=ID_ONE, type="page"))

        assert builder.render_in_progress() == f"({ID_ONE}, page) -> "

    def test_render_complete_has_no_trailing_arrow(self):
        builder = ChainLineBuilder()
        builder.start(IdentifierType(id=ID_ONE, type="page"))
        builder.append_step("READ")

        assert builder.render_complete() == f"({ID_ONE}, page) READ"
        assert not builder.render_complete().endswith("-> ")


# ============================================================================
# ChainLineBuilder: error column alignment
# ============================================================================

class TestChainLineBuilderErrorAlignment:
    def test_error_at_first_step_short_prefix(self):
        builder = ChainLineBuilder()
        builder.start(IdentifierType(id=ID_ONE, type="page"))
        builder.append_step("READ")  # the failing step's own (bare) label

        v_line, error_block = builder.render_error(0, "Asset not found", "driver.py", 42)

        prefix_len = len(f"({ID_ONE}, page) ")
        assert v_line == " " * prefix_len + "v"
        assert error_block == " " * prefix_len + "!ERROR: Asset not found @driver.py:42"
        # The 'v' must land directly under the failing label's first char.
        rendered_so_far = builder.render_complete()
        assert rendered_so_far[len(v_line) - 1] == "R"

    def test_error_at_second_step_varying_label_lengths(self):
        builder = ChainLineBuilder()
        builder.start(IdentifierType(id=ID_ONE, type="page"))
        builder.append_step("READ")
        builder.append_step("a_very_long_callback_name_here")  # fails here (unresolved)

        v_line, error_block = builder.render_error(1, "boom", "operations.py", 7)

        prefix_len = len(f"({ID_ONE}, page) READ -> ")
        assert v_line == " " * prefix_len + "v"
        assert error_block == " " * prefix_len + "!ERROR: boom @operations.py:7"
        rendered_so_far = builder.render_complete()
        assert rendered_so_far[len(v_line) - 1] == "a"  # first char of the failing label

    def test_error_at_third_step_short_and_long_labels_mixed(self):
        builder = ChainLineBuilder()
        builder.start(IdentifierType(id=ID_ONE, type="page"))
        builder.append_step("READ")
        builder.append_step("change_displayName: Asset")
        builder.append_step("EDIT")  # fails here

        v_line, error_block = builder.render_error(
            2, "CascadeError: permission denied", "operations.py", 913
        )

        prefix_len = len(f"({ID_ONE}, page) READ -> change_displayName: Asset -> ")
        assert v_line == " " * prefix_len + "v"
        assert error_block == (
            " " * prefix_len + "!ERROR: CascadeError: permission denied @operations.py:913"
        )
        rendered_so_far = builder.render_complete()
        assert rendered_so_far[len(v_line) - 1] == "E"

    def test_arrow_separator_is_four_characters_not_two(self):
        """The most likely off-by-one: " -> " is 4 chars, not 2."""
        builder = ChainLineBuilder()
        builder.start(IdentifierType(id=ID_ONE, type="page"))
        builder.append_step("AB")  # a short, precisely-measurable label
        builder.append_step("CD")

        v_line, _ = builder.render_error(1, "x", "f.py", 1)

        expected_column = len(f"({ID_ONE}, page) ") + len("AB") + 4  # " -> " == 4 chars
        assert len(v_line) - 1 == expected_column


# ============================================================================
# ChainLineBuilder: multi-line error message indentation
# ============================================================================

class TestChainLineBuilderMultilineError:
    def test_continuation_lines_match_first_line_indent(self):
        builder = ChainLineBuilder()
        builder.start(IdentifierType(id=ID_ONE, type="page"))
        builder.append_step("EDIT")

        v_line, error_block = builder.render_error(
            0, "line one\nline two\nline three", "f.py", 5
        )

        lines = error_block.split("\n")
        assert len(lines) == 3
        indent = " " * (len(v_line) - 1)
        assert lines[0] == f"{indent}!ERROR: line one"
        assert lines[1] == f"{indent}line two"
        assert lines[2] == f"{indent}line three @f.py:5"
        # No progressive indent: every continuation line has the SAME indent.
        assert all(line.startswith(indent) for line in lines)
        assert all(
            len(line) - len(line.lstrip(" ")) == len(indent) for line in lines
        )


# ============================================================================
# Request/response detail lines
# ============================================================================

class TestRequestDetailLine:
    def test_with_payload_reference(self, tmp_path):
        logger = OperationLogger(server="test", debug_config={"log_dir": str(tmp_path)})
        logger.log_request_detail("POST", "https://x/edit", payload_ref="abc123_request.json")

        lines = _logged_lines(logger)
        assert "[POST] https://x/edit | payload: abc123_request.json" in lines

    def test_without_payload_reference(self, tmp_path):
        logger = OperationLogger(server="test", debug_config={"log_dir": str(tmp_path)})
        logger.log_request_detail("GET", "https://x/read/page/abc")

        lines = _logged_lines(logger)
        assert "[GET] https://x/read/page/abc" in lines
        assert not any("payload" in line for line in lines)

    def test_not_written_in_normal_mode(self, tmp_path, monkeypatch):
        """Normal mode's logfile is just the per-chain pipeline line plus
        the batch tally — no per-request [METHOD] url detail."""
        monkeypatch.chdir(tmp_path)
        logger = OperationLogger(server="test")
        logger.log_request_detail("GET", "https://x/read/page/abc")

        lines = _logged_lines(logger)
        assert not any("[GET]" in line for line in lines)


# ============================================================================
# File output (verbose mode only)
# ============================================================================

class TestFileOutput:
    def test_request_file_written_in_debug_mode(self, tmp_path):
        logger = OperationLogger(server="test", debug_config={"log_dir": str(tmp_path)})
        logger.write_request_file("abc123", {"page": {"name": "Updated"}})

        written = tmp_path / "abc123_request.json"
        assert written.exists()
        assert json.loads(written.read_text()) == {"page": {"name": "Updated"}}

    def test_response_file_skipped_for_trivial_success_shape(self, tmp_path):
        logger = OperationLogger(server="test", debug_config={"log_dir": str(tmp_path)})
        logger.write_response_file("abc123", {"success": True})

        assert not (tmp_path / "abc123_response.json").exists()

    def test_response_file_skipped_for_trivial_failure_shape(self, tmp_path):
        logger = OperationLogger(server="test", debug_config={"log_dir": str(tmp_path)})
        logger.write_response_file("abc123", {"success": False, "message": "not found"})

        assert not (tmp_path / "abc123_response.json").exists()

    def test_response_file_written_for_structured_content(self, tmp_path):
        logger = OperationLogger(server="test", debug_config={"log_dir": str(tmp_path)})
        logger.write_response_file(
            "abc123", {"asset": {"page": {"id": ID_ONE, "name": "Home"}}}
        )

        written = tmp_path / "abc123_response.json"
        assert written.exists()
        assert json.loads(written.read_text())["asset"]["page"]["name"] == "Home"

    def test_no_files_written_outside_debug_mode(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        logger = OperationLogger(server="test", debug_config=None)

        logger.write_request_file("abc123", {"a": 1})
        logger.write_response_file("abc123", {"asset": {"page": {"id": ID_ONE}}})

        assert not list(tmp_path.rglob("*_request.json"))
        assert not list(tmp_path.rglob("*_response.json"))

    def test_accepts_raw_bytes_as_well_as_dict(self, tmp_path):
        logger = OperationLogger(server="test", debug_config={"log_dir": str(tmp_path)})
        logger.write_request_file("abc123", json.dumps({"a": 1}).encode())

        written = tmp_path / "abc123_request.json"
        assert json.loads(written.read_text()) == {"a": 1}


# ============================================================================
# Batch start/end framing
# ============================================================================

class TestBatchFraming:
    def test_debug_mode_writes_start_end_markers_and_tally(self, tmp_path):
        logger = OperationLogger(server="test", debug_config={"log_dir": str(tmp_path)})
        logger._script_name = "script.py"

        logger.log_batch_start()
        logger.log_batch_end(succeeded=2, total=3)

        lines = _logged_lines(logger)
        assert ">>>> START REQUEST <<<<" in lines
        assert "2/3 succeeded" in lines
        assert ">>>> END REQUEST <<<<" in lines
        assert lines.index(">>>> START REQUEST <<<<") < lines.index("2/3 succeeded")
        assert lines.index("2/3 succeeded") < lines.index(">>>> END REQUEST <<<<")

    def test_multiple_batches_get_independent_pairs(self, tmp_path):
        logger = OperationLogger(server="test", debug_config={"log_dir": str(tmp_path)})

        logger.log_batch_start()
        logger.log_batch_end(succeeded=1, total=1)
        logger.log_batch_start()
        logger.log_batch_end(succeeded=0, total=2)

        lines = _logged_lines(logger)
        assert lines.count(">>>> START REQUEST <<<<") == 2
        assert lines.count(">>>> END REQUEST <<<<") == 2
        assert "1/1 succeeded" in lines
        assert "0/2 succeeded" in lines

    def test_tally_accumulates_into_session_totals(self, tmp_path):
        logger = OperationLogger(server="test", debug_config={"log_dir": str(tmp_path)})

        logger.log_batch_start()
        logger.log_batch_end(succeeded=2, total=3)
        logger.log_batch_start()
        logger.log_batch_end(succeeded=1, total=1)

        assert logger._processed_count == 4
        assert logger._error_count == 1


# ============================================================================
# Normal (non-debug) mode
# ============================================================================

class TestNormalMode:
    def test_no_file_writes_outside_debug(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        logger = OperationLogger(server="APPTEST", debug_config=None)
        logger.write_request_file("x", {"a": 1})
        logger.write_response_file("x", {"asset": {}})

        assert not list(tmp_path.rglob("*.json"))

    def test_minimal_console_lifecycle_messages(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        logger = OperationLogger(server="APPTEST", debug_config=None)
        # `cascade.console` is a shared, name-cached logger whose handler's
        # stream was bound at whatever `sys.stdout` existed the first time
        # any OperationLogger was built in this process — capsys swaps
        # sys.stdout per-test, so it can miss console output entirely.
        # Redirecting the handler's stream directly sidesteps that.
        buffer = io.StringIO()
        logger._console_logger.handlers[0].stream = buffer

        logger.log_init("https://cascade.example", "script.py")
        logger.log_batch_start()
        logger.log_batch_end(succeeded=1, total=1)
        logger.log_exit()

        out = buffer.getvalue()
        assert "[INIT]: Connecting to APPTEST" in out
        assert "[RUNNING]: script.py" in out
        assert "1/1 succeeded" in out
        assert "[DONE]:" in out
        assert "[EXIT]: Disconnecting from APPTEST" in out
        # Debug-only chatter must not leak into normal mode.
        assert "[DEBUG]" not in out


# ============================================================================
# Chain-agnostic error entry points
# ============================================================================

class TestStandaloneErrorEntryPoints:
    def test_log_cascade_error_writes_v_error_block(self, tmp_path):
        logger = OperationLogger(server="test", debug_config={"log_dir": str(tmp_path)})
        identifier = IdentifierType(id=ID_ONE, type="page")

        logger.log_cascade_error("Asset not found", identifier)

        lines = _logged_lines(logger)
        assert any(line.strip() == "v" for line in lines)
        assert any("!ERROR: Asset not found" in line for line in lines)

    def test_log_python_error_includes_type_message_and_location(self, tmp_path):
        logger = OperationLogger(server="test", debug_config={"log_dir": str(tmp_path)})
        try:
            raise ValueError("bad data")
        except ValueError as exc:
            logger.log_python_error(exc)

        lines = _logged_lines(logger)
        assert any(line.strip() == "v" for line in lines)
        error_line = next(line for line in lines if "!ERROR:" in line)
        assert "ValueError: bad data" in error_line
        assert "test_operation_logger.py" in error_line
