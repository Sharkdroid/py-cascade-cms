from pathlib import Path
from uuid import UUID

from cascade_cms.cmstypes import (
    CascadeError,
    parse_workflow_information,
    workflowInformation,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_parses_wrapped_workflow_response():
    content = parse_workflow_information(load("read_workflow_information.json"))._content

    assert isinstance(content, workflowInformation)
    wf = content.workflow
    assert wf.current_step == "review-approver"
    assert len(wf.ordered_steps) == 5
    assert len(wf.unordered_steps) == 3
    assert wf.workflow_info_id == UUID("b4ced508ac10010f176d517740bb9629")
    assert wf.related_entity.path is not None
    assert wf.related_entity.path.path == "programs_hd1/welding-technology/admission-steps"


def test_system_steps_without_owner_parse_with_owner_none():
    wf = parse_workflow_information(load("read_workflow_information.json"))._content.workflow

    system_steps = [s for s in wf.ordered_steps if s.step_type == "system"]
    assert [s.step_identifier for s in system_steps] == ["initialize", "finalize", "finish"]
    assert all(s.owner is None for s in system_steps)


def test_cascade_error_body_still_surfaces_as_cascade_error():
    raw = b'{"success": false, "message": "Asset is not in a workflow"}'

    content = parse_workflow_information(raw)._content

    assert isinstance(content, CascadeError)
    assert content.message == "Asset is not in a workflow"
