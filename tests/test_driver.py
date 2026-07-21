from cascade_cms.cmstypes import CascadeError, ResponseParser, simple_payload_adapter


def test_response_parser_prefers_cascade_error():
    raw = b'{"success": false, "message": "not found"}'
    parsed = ResponseParser(raw=raw, serializer=simple_payload_adapter)

    assert isinstance(parsed._content, CascadeError)
    assert parsed._content.message == "not found"
    assert parsed._cacheable is False


# TODO: cover RequestExecutor.fetch's caching path with a mocked aiohttp
# ClientSession (e.g. aioresponses) once the dev dependency is added.
