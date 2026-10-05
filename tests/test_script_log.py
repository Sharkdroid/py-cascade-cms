"""A12: `script_log.note()` routing through the active run."""

import threading
import warnings

import pytest
from test_wrapper import ID_ONE, StubDriver, make_asset, make_wrapper

from cascade_cms import Cascade
from cascade_cms.cmstypes import IdentifierType
from cascade_cms.utils import script_log
from cascade_cms.utils.operation_logger import OperationLogger

KEY = "supersecretkey-wxyz"


def _open(tmp_path, name="a", api_key=""):
    logger = OperationLogger(server=name, debug_config={"log_dir": str(tmp_path / name)})
    wrapper = make_wrapper(StubDriver([]), logger=logger)
    wrapper._api_key = api_key
    return wrapper, logger


def _lines(logger):
    handler = logger._file_logger.handlers[0]
    handler.flush()
    return logger.log_path.read_text().splitlines()


def test_note_inside_block_reaches_logfile(tmp_path):
    wrapper, logger = _open(tmp_path)
    with wrapper:
        script_log.note("will delete page abc /news/old-1")
    assert "[NOTE]: will delete page abc /news/old-1" in _lines_after(logger)


def _lines_after(logger):
    # The handler is closed at exit; read the file directly.
    return logger.log_path.read_text().splitlines()


def test_note_outside_block_warns_and_is_ignored(tmp_path):
    wrapper, logger = _open(tmp_path)
    with pytest.warns(RuntimeWarning, match="no active"):
        script_log.note("before")
    with wrapper:
        pass
    with pytest.warns(RuntimeWarning, match="no active"):
        script_log.note("after")
    assert not any("[NOTE]" in ln for ln in _lines_after(logger))


def test_note_after_exception_in_block_is_not_routed(tmp_path):
    wrapper, _ = _open(tmp_path)
    with pytest.raises(KeyError), wrapper:
        raise KeyError("x")
    with pytest.warns(RuntimeWarning):
        script_log.note("late")


def test_two_wrappers_on_two_threads_keep_notes_separate(tmp_path):
    barrier = threading.Barrier(2)
    logs = {}

    def work(name):
        wrapper, logger = _open(tmp_path, name)
        logs[name] = logger
        with wrapper:
            barrier.wait(5)  # both blocks are open at the same time
            for i in range(20):
                script_log.note(f"{name}-{i}")
            barrier.wait(5)

    threads = [threading.Thread(target=work, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    for name, other in (("a", "b"), ("b", "a")):
        text = "\n".join(_lines_after(logs[name]))
        assert f"[NOTE]: {name}-19" in text
        assert f"{other}-" not in text.replace(f"{name}-", "")


def test_thread_pool_callback_note_reaches_correct_file(tmp_path):
    driver = StubDriver([[make_asset(id=ID_ONE)]])
    logger = OperationLogger(server="T", debug_config={"log_dir": str(tmp_path)})
    wrapper = make_wrapper(driver, logger=logger)
    wrapper._api_key = ""

    def callback(asset):
        script_log.note("from callback")

    wrapper.operations.read(IdentifierType(id=ID_ONE, type="page")).then(callback)
    with wrapper:
        wrapper.submit_requests()
    assert "[NOTE]: from callback" in _lines_after(logger)


def test_explicit_thread_pool_executor_callback_note(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    driver = StubDriver([[make_asset(id=ID_ONE)]])
    logger = OperationLogger(server="T", debug_config={"log_dir": str(tmp_path)})
    wrapper = make_wrapper(driver, logger=logger)
    wrapper._api_key = ""

    def callback(asset):
        script_log.note("pool note")

    wrapper.operations.read(IdentifierType(id=ID_ONE, type="page")).then(callback)
    with wrapper, ThreadPoolExecutor(max_workers=1) as pool:
        wrapper.submit_requests(executor=pool)
    assert "[NOTE]: pool note" in _lines_after(logger)


def test_note_with_api_key_is_masked(tmp_path):
    wrapper, logger = _open(tmp_path, api_key=KEY)
    with wrapper:
        script_log.note(f"token is {KEY} ok")
    text = "\n".join(_lines_after(logger))
    assert KEY not in text
    assert "[NOTE]: token is ****wxyz ok" in text


def test_newlines_collapse_to_one_line(tmp_path):
    wrapper, logger = _open(tmp_path)
    with wrapper:
        script_log.note("a\nb\r\nc")
    assert "[NOTE]: a b  c" in _lines_after(logger)


def test_real_wrapper_routes_and_resets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = {"SERVER": "T", "API_KEY": KEY, "CASCADE_URL": "http://127.0.0.1:1"}
    with Cascade(env) as cascade:
        script_log.note(f"k={KEY}")
        path = cascade._logger.log_path
    assert "[NOTE]: k=****wxyz" in path.read_text()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        script_log.note("after")
    assert any(issubclass(w.category, RuntimeWarning) for w in caught)


def test_import_path_and_root_reexport():
    from cascade_cms import OperationLogger as Root
    from cascade_cms.utils.operation_logger import OperationLogger as Moved

    assert Root is Moved
    with pytest.raises(ImportError):
        import cascade_cms.operation_logger  # noqa: F401
