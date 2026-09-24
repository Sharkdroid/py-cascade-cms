from cascade_cms.cmstypes import CascadeError, ResponseParser, simple_payload_adapter


def test_response_parser_prefers_cascade_error():
    raw = b'{"success": false, "message": "not found"}'
    parsed = ResponseParser(raw=raw, serializer=simple_payload_adapter)

    assert isinstance(parsed._content, CascadeError)
    assert parsed._content.message == "not found"



def test_close_clears_current_event_loop():
    import asyncio

    import pytest

    from cascade_cms.driver import CascadeCMSRestDriver

    driver = CascadeCMSRestDriver("token", "http://127.0.0.1:1")
    driver.close()
    assert driver.eventLoop.is_closed()
    with pytest.raises(RuntimeError):
        asyncio.get_event_loop()


def test_response_parser_and_executor_require_their_arguments():
    import pytest

    from cascade_cms.driver import RequestExecutor

    with pytest.raises(TypeError):
        ResponseParser(raw=b"{}")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        RequestExecutor("http://x", "GET")  # type: ignore[call-arg]
