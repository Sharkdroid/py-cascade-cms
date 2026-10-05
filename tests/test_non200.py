"""Non-200 responses become CASCADE failures (status + reason, no body)."""

import asyncio
import threading

import pytest
from aiohttp import web

from cascade_cms import Cascade
from cascade_cms.cmstypes import CascadeError, IdentifierType
from cascade_cms.utils.failures import FailureCategory

HTML = "<html><body>SECRET-TOMCAT-PAGE</body></html>"
ID = "8b320f55ac1001062545a6d2562cee4b"


@pytest.fixture
def server():
    status = {"code": 200}

    async def handler(request):
        return web.Response(
            status=status["code"],
            text=HTML,
            content_type="text/html",
            headers={
                "Set-Cookie": "JSESSIONID=abc; Path=/; Secure; HttpOnly; "
                "SameSite=None; Partitioned"
            },
        )

    loop = asyncio.new_event_loop()
    runner = web.AppRunner(_app(handler))
    ready = threading.Event()
    info = {}

    async def start():
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        info["port"] = site._server.sockets[0].getsockname()[1]
        ready.set()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    ready.wait(5)
    yield info["port"], status
    fut = asyncio.run_coroutine_threadsafe(runner.cleanup(), loop)
    fut.result(5)
    loop.call_soon_threadsafe(loop.stop)
    t.join(5)


def _app(handler):
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    return app


@pytest.mark.parametrize(
    "code,reason",
    [
        (401, "Unauthorized"),
        (403, "Forbidden"),
        (404, "Not Found"),
        (500, "Internal Server Error"),
        (501, "Not Implemented"),
    ],
)
def test_non_200_is_cascade_failure(server, tmp_path, monkeypatch, code, reason):
    monkeypatch.chdir(tmp_path)
    port, status = server
    status["code"] = code
    env = {
        "SERVER": "TEST",
        "API_KEY": "token-abcd",
        "CASCADE_URL": f"http://127.0.0.1:{port}",
    }
    with Cascade(env, exit_on_failure=False) as cascade:
        cascade.operations.read(IdentifierType(id=ID, type="page"))
        results = cascade.submit_requests()

    assert isinstance(results[0], CascadeError)
    assert results[0].message == f"{code} {reason}"
    failure = results.failed[0]
    assert failure.category is FailureCategory.CASCADE
    assert "SECRET-TOMCAT-PAGE" not in failure.message
    for log in (tmp_path / "logs").rglob("*"):
        if log.is_file():
            assert "SECRET-TOMCAT-PAGE" not in log.read_text()


def test_partitioned_cookie_does_not_break_requests(server, tmp_path, monkeypatch):
    """A4: Cascade's Set-Cookie carries `Partitioned`; with the response
    cache gone, aiohttp handles it without raising, so no compat patch is
    needed in the library."""
    monkeypatch.chdir(tmp_path)
    port, _ = server

    import aiohttp

    async def go():
        async with (
            aiohttp.ClientSession() as s,
            s.get(f"http://127.0.0.1:{port}/cookie") as r,
        ):
            return r.status

    app_loop = asyncio.new_event_loop()
    try:
        assert app_loop.run_until_complete(go()) == 200
    finally:
        app_loop.close()
