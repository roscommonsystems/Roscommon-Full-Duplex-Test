import asyncio
import pytest
from aiohttp import web, WSMsgType, ClientSession
from supervisor.app import create_app
from supervisor.registry import ModelRegistry


class FakeChild:
    def __init__(self, port):
        self.state = "ready"
        self.current_repo = "repo-a"
        self.error = None
        self.port = port

    @property
    def is_busy(self):
        return False

    def request_switch(self, repo):
        pass


@pytest.fixture
async def backend(aiohttp_server):
    async def ping(request):
        return web.json_response({"pong": request.query.get("q")})

    async def chat(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                await ws.send_bytes(b"echo:" + msg.data)
        return ws

    async def pushchat(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str("hello-text")
        await ws.send_bytes(b"server-push")
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/api/ping", ping)
    app.router.add_get("/api/chat", chat)
    app.router.add_get("/api/pushchat", pushchat)
    return await aiohttp_server(app)


async def test_http_proxied_to_child(aiohttp_client, backend):
    app = create_app(ModelRegistry([{"id": "repo-a", "name": "A"}]), FakeChild(backend.port))
    client = await aiohttp_client(app)
    resp = await client.get("/api/ping?q=hi")
    assert resp.status == 200
    assert (await resp.json()) == {"pong": "hi"}


async def test_ws_proxied_to_child(aiohttp_client, backend):
    app = create_app(ModelRegistry([{"id": "repo-a", "name": "A"}]), FakeChild(backend.port))
    client = await aiohttp_client(app)
    ws = await client.ws_connect("/api/chat")
    await ws.send_bytes(b"hello")
    msg = await asyncio.wait_for(ws.receive(), timeout=5)
    assert msg.data == b"echo:hello"
    await ws.close()


async def test_ws_server_push_text_and_binary(aiohttp_client, backend):
    app = create_app(ModelRegistry([{"id": "repo-a", "name": "A"}]), FakeChild(backend.port))
    client = await aiohttp_client(app)
    ws = await client.ws_connect("/api/pushchat")
    m1 = await asyncio.wait_for(ws.receive(), timeout=5)
    m2 = await asyncio.wait_for(ws.receive(), timeout=5)
    got = {m1.data, m2.data}
    assert "hello-text" in got
    assert b"server-push" in got
    await ws.close()


async def test_static_index_fallback(aiohttp_client, tmp_path):
    (tmp_path / "index.html").write_text("<h1>app</h1>", encoding="utf-8")
    app = create_app(ModelRegistry([{"id": "repo-a", "name": "A"}]),
                     FakeChild(1), static_dir=str(tmp_path))
    client = await aiohttp_client(app)
    resp = await client.get("/some/spa/route")
    assert resp.status == 200
    assert "<h1>app</h1>" in await resp.text()


async def test_proxy_http_503_when_not_ready(aiohttp_client, backend):
    child = FakeChild(backend.port)
    child.state = "loading"
    app = create_app(ModelRegistry([{"id": "repo-a", "name": "A"}]), child)
    client = await aiohttp_client(app)
    resp = await client.get("/api/ping")
    assert resp.status == 503


async def test_proxy_ws_rejected_when_not_ready(aiohttp_client, backend):
    import aiohttp
    child = FakeChild(backend.port)
    child.state = "loading"
    app = create_app(ModelRegistry([{"id": "repo-a", "name": "A"}]), child)
    client = await aiohttp_client(app)
    with pytest.raises(aiohttp.WSServerHandshakeError):
        await client.ws_connect("/api/chat")
