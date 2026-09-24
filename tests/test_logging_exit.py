"""A5: stderr status lines, [LOG] line, tally + [EXIT-CODE] in the logfile,
released file handles, unique log names, batch failure prefix."""

import io
import subprocess
import sys
import textwrap

import aiohttp
import pytest
from test_wrapper import ID_ONE, StubDriver, make_asset, make_wrapper

from cascade_cms.cmstypes import CascadeError, IdentifierType
from cascade_cms.failures import CascadeBatchError
from cascade_cms.utils.operation_logger import OperationLogger


def _logger(tmp_path):
    logger = OperationLogger(server="T", debug_config={"log_dir": str(tmp_path)})
    logger._console_logger.handlers[0].stream = io.StringIO()
    return logger


def _text(logger):
    return logger.log_path.read_text()


def _read(wrapper):
    wrapper.operations.read(IdentifierType(id=ID_ONE, type="page"))


def _exit(wrapper, exc=None):
    """Run __exit__ as a `with` block would; return (raised, exception)."""
    exc_type = type(exc) if exc else None
    try:
        wrapper.__exit__(exc_type, exc, None)
    except BaseException as raised:  # noqa: BLE001
        return raised
    return None


def test_clean_run_logs_tally_and_exit_code_0(tmp_path):
    driver = StubDriver([[make_asset(id=ID_ONE)]])
    logger = _logger(tmp_path)
    wrapper = make_wrapper(driver, logger=logger)
    _read(wrapper)
    wrapper.submit_requests()
    assert _exit(wrapper) is None
    text = _text(logger)
    assert "0 failed, 1 succeeded" in text
    assert text.rstrip().endswith("[EXIT-CODE]: 0")


def test_cascade_failure_exit_code_1(tmp_path):
    driver = StubDriver([[CascadeError(message="denied")]])
    logger = _logger(tmp_path)
    wrapper = make_wrapper(driver, logger=logger)
    _read(wrapper)
    wrapper.submit_requests()
    assert isinstance(_exit(wrapper), SystemExit)
    text = _text(logger)
    assert "1 failed, 0 succeeded" in text
    assert text.rstrip().endswith("[EXIT-CODE]: 1")


def test_callback_exception_exit_code(tmp_path):
    driver = StubDriver([[make_asset(id=ID_ONE)]])
    logger = _logger(tmp_path)
    wrapper = make_wrapper(driver, logger=logger)

    def boom(_a):
        raise ValueError("x")

    wrapper.operations.read(IdentifierType(id=ID_ONE, type="page")).then(boom)
    wrapper.submit_requests()
    assert isinstance(_exit(wrapper), ValueError)
    assert "[EXIT-CODE]: 1 (callback exception: ValueError)" in _text(logger)


def test_uncaught_exception_exit_code(tmp_path):
    logger = _logger(tmp_path)
    wrapper = make_wrapper(StubDriver([]), logger=logger)
    assert _exit(wrapper, KeyError("k")) is None  # propagates via return False
    assert "[EXIT-CODE]: 1 (uncaught exception: KeyError)" in _text(logger)


def test_keyboard_interrupt_is_uncaught(tmp_path):
    logger = _logger(tmp_path)
    wrapper = make_wrapper(StubDriver([]), logger=logger)
    _exit(wrapper, KeyboardInterrupt())
    assert "[EXIT-CODE]: 1 (uncaught exception: KeyboardInterrupt)" in _text(logger)


def test_in_flight_system_exit_logs_its_code(tmp_path):
    logger = _logger(tmp_path)
    wrapper = make_wrapper(StubDriver([]), logger=logger)
    _exit(wrapper, SystemExit(2))
    assert "[EXIT-CODE]: 2" in _text(logger)


def test_batch_error_exit_code(tmp_path):
    logger = _logger(tmp_path)
    wrapper = make_wrapper(StubDriver([]), logger=logger)
    assert isinstance(_exit(wrapper, CascadeBatchError("x")), SystemExit)
    assert _text(logger).rstrip().endswith("[EXIT-CODE]: 1")


def test_exit_on_failure_disabled_is_na_when_nothing_in_flight(tmp_path):
    driver = StubDriver([[CascadeError(message="denied")]])
    logger = _logger(tmp_path)
    wrapper = make_wrapper(driver, logger=logger, exit_on_failure=False)
    _read(wrapper)
    wrapper.submit_requests()
    assert _exit(wrapper) is None
    assert "[EXIT-CODE]: n/a (exit_on_failure disabled)" in _text(logger)


def test_exit_on_failure_disabled_exception_wins(tmp_path):
    logger = _logger(tmp_path)
    wrapper = make_wrapper(StubDriver([]), logger=logger, exit_on_failure=False)
    _exit(wrapper, CascadeBatchError("x"))
    assert "[EXIT-CODE]: 1 (uncaught exception: CascadeBatchError)" in _text(logger)


def test_file_handler_closed_after_exit(tmp_path):
    logger = _logger(tmp_path)
    wrapper = make_wrapper(StubDriver([]), logger=logger)
    handler = logger._file_logger.handlers[0]
    _exit(wrapper)
    assert logger._file_logger.handlers == []
    assert handler.stream is None


def test_two_loggers_same_second_get_different_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = OperationLogger(server="T")
    b = OperationLogger(server="T")
    assert a.log_path != b.log_path
    a.close()
    b.close()


def test_log_path_line_printed_after_init(tmp_path):
    logger = _logger(tmp_path)
    logger.log_init("https://x", "s.py")
    lines = logger._console_logger.handlers[0].stream.getvalue().splitlines()
    assert lines[0].startswith("[INIT]")
    assert lines[1] == f"[LOG]: {logger.log_path}"


def test_batch_failure_prefix_follows_cause(tmp_path):
    for exc, prefix in [
        (ConnectionError("down"), "[ERROR]: [NETWORK] ConnectionError"),
        (
            aiohttp.ServerDisconnectedError(),
            "[ERROR]: [NETWORK] ServerDisconnectedError",
        ),
        (RuntimeError("loop"), "[ERROR]: [CASCADE-REST-CMS] RuntimeError"),
    ]:
        logger = _logger(tmp_path)
        driver = StubDriver([])
        wrapper = make_wrapper(driver, logger=logger)
        _read(wrapper)

        async def broken(chains, executor=None, exc=exc):
            raise exc

        wrapper._execute_chains = broken
        try:
            with pytest.raises(CascadeBatchError):
                wrapper.submit_requests()
        finally:
            driver.eventLoop.close()
        assert prefix in logger._console_logger.handlers[0].stream.getvalue()


def test_stdout_empty_for_normal_run():
    script = textwrap.dedent(
        """
        from tests.test_wrapper import StubDriver, make_wrapper, make_asset
        from cascade_cms.cmstypes import IdentifierType
        from cascade_cms.utils.operation_logger import OperationLogger
        import tempfile, os
        os.chdir(tempfile.mkdtemp())
        driver = StubDriver([[make_asset(id="8b320f55ac1001062545a6d2562cee4b")]])
        wrapper = make_wrapper(driver, logger=OperationLogger(server="T"))
        wrapper._logger.log_init("https://x", "s.py")
        wrapper.operations.read(IdentifierType(id="8b320f55ac1001062545a6d2562cee4b", type="page"))
        with wrapper:
            wrapper.submit_requests()
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "[INIT]" in result.stderr and "[LOG]:" in result.stderr
    assert "[EXIT]" in result.stderr


def test_log_dir_created_and_used_in_normal_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "nested" / "logs"
    logger = OperationLogger(server="T", log_dir=target)
    assert logger.log_path.parent == target
    assert not (tmp_path / "logs").exists()
    logger.close()


def test_explicit_log_dir_beats_debug_config(tmp_path):
    cfg_dir, explicit = tmp_path / "cfg", tmp_path / "explicit"
    logger = OperationLogger(
        server="T", debug_config={"log_dir": str(cfg_dir)}, log_dir=explicit
    )
    assert logger.log_path.parent == explicit
    assert not cfg_dir.exists()
    logger.close()


def test_wrapper_passes_log_dir_to_logger(tmp_path, monkeypatch):
    from cascade_cms import CascadeWrapperBase

    monkeypatch.chdir(tmp_path)
    env = {"SERVER": "T", "API_KEY": "k", "CASCADE_URL": "http://127.0.0.1:1"}
    with CascadeWrapperBase(env, log_dir=tmp_path / "custom") as cascade:
        assert cascade._logger.log_path.parent == tmp_path / "custom"
    assert not (tmp_path / "logs").exists()
