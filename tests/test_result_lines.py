"""A11: automatic `[RESULT]` lines for successful write operations."""

from test_wrapper import ID_ONE, ID_TWO, StubDriver, make_asset, make_wrapper

from cascade_cms.cmstypes import (
    CascadeError,
    CascadeSuccess,
    IdentifierType,
    NewAsset,
    Path,
    SiteCopyParameter,
)
from cascade_cms.utils.operation_logger import OperationLogger

NEW_ID = "5f1a0000000000000000000000c90d00"


def _new(name="copy"):
    return NewAsset(
        name=name, asset_type="page", site_name="S", parent_folder_path="/_dev/testing"
    )


def _run(tmp_path, responses, build):
    logger = OperationLogger(server="T", debug_config={"log_dir": str(tmp_path)})
    driver = StubDriver(responses)
    wrapper = make_wrapper(driver, logger=logger)
    build(wrapper.operations)
    try:
        results = wrapper.submit_requests()
    finally:
        driver.eventLoop.close()
    lines = logger.log_path.read_text().splitlines()
    return results, [ln for ln in lines if ln.startswith("[RESULT]")], lines


def test_create_yields_result_with_new_identifier_and_path(tmp_path):
    created = IdentifierType(id=NEW_ID, type="page")
    _, results, lines = _run(
        tmp_path, [[created]], lambda ops: ops.create(_new("testbed2-copy"))
    )
    assert results == [f"[RESULT]: create page {NEW_ID} /_dev/testing/testbed2-copy"]
    # After the chain's own pipeline line.
    assert lines.index(results[0]) > 0


def test_delete_yields_result_with_target(tmp_path):
    _, results, _ = _run(
        tmp_path,
        [[CascadeSuccess()]],
        lambda ops: ops.delete(IdentifierType(id=ID_ONE, type="page")),
    )
    assert results == [f"[RESULT]: delete page {ID_ONE} succeeded"]


def test_delete_by_path_names_the_path(tmp_path):
    path = Path(asset_type="page", site_name="S", path="/news/old-1")
    _, results, _ = _run(tmp_path, [[CascadeSuccess()]], lambda ops: ops.delete(path))
    assert results == ["[RESULT]: delete page /news/old-1 succeeded"]


def test_failed_write_yields_no_result(tmp_path):
    _, results, _ = _run(
        tmp_path,
        [[CascadeError(message="denied")]],
        lambda ops: ops.delete(IdentifierType(id=ID_ONE, type="page")),
    )
    assert results == []


def test_read_yields_no_result(tmp_path):
    _, results, _ = _run(
        tmp_path,
        [[make_asset(id=ID_ONE)]],
        lambda ops: ops.read(IdentifierType(id=ID_ONE, type="page")),
    )
    assert results == []


def test_list_create_with_one_failure_yields_one_result(tmp_path):
    created = IdentifierType(id=NEW_ID, type="page")
    _, results, _ = _run(
        tmp_path,
        [[created, CascadeError(message="dup")]],
        lambda ops: ops.create([_new("a"), _new("b")]),
    )
    assert results == [f"[RESULT]: create page {NEW_ID} /_dev/testing/a"]


def test_write_then_later_failure_keeps_the_result(tmp_path):
    """A stage that succeeded stays on record even if the chain stops later."""
    created = IdentifierType(id=NEW_ID, type="page")

    def boom(_r):
        raise ValueError("later step failed")

    _, results, _ = _run(
        tmp_path, [[created]], lambda ops: ops.create(_new("x")).then(boom)
    )
    assert results == [f"[RESULT]: create page {NEW_ID} /_dev/testing/x"]


def test_target_less_write_names_only_the_operation(tmp_path):
    _, results, _ = _run(
        tmp_path,
        [[CascadeSuccess()]],
        lambda ops: ops.siteCopy(
            SiteCopyParameter(originalSiteName="A", newSiteName="B")  # type: ignore[call-arg]
        ),
    )
    assert results == ["[RESULT]: siteCopy succeeded"]


def test_second_target_id_is_independent(tmp_path):
    _, results, _ = _run(
        tmp_path,
        [[CascadeSuccess()], [CascadeSuccess()]],
        lambda ops: [
            ops.delete(IdentifierType(id=ID_ONE, type="page")),
            ops.delete(IdentifierType(id=ID_TWO, type="page")),
        ],
    )
    assert results == [
        f"[RESULT]: delete page {ID_ONE} succeeded",
        f"[RESULT]: delete page {ID_TWO} succeeded",
    ]
