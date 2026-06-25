import asyncio
import pytest
from aiohttp import web, WSMsgType
from supervisor.app import create_app
from supervisor.registry import ModelRegistry

MODELS = [{"id": "repo-a", "name": "A"}]


class FakeChild:
    state = "ready"
    current_repo = "repo-a"
    error = None
    port = 8999

    @property
    def is_busy(self):
        return False

    def request_switch(self, repo):
        pass


class FakeAsr:
    def __init__(self, port, available=True):
        self.port = port
        self._available = available

    @property
    def available(self):
        return self._available


@pytest.fixture
async def asr_backend(aiohttp_server):
    async def transcribe(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                await ws.send_str("you-said")
        return ws

    app = web.Application()
    app.router.add_get("/api/transcribe", transcribe)
    return await aiohttp_server(app)


async def test_available_false_without_asr(aiohttp_client):
    app = create_app(ModelRegistry(MODELS), FakeChild(), asr=None)
    client = await aiohttp_client(app)
    resp = await client.get("/api/transcribe/available")
    assert (await resp.json()) == {"available": False}


async def test_available_true_with_ready_asr(aiohttp_client):
    app = create_app(ModelRegistry(MODELS), FakeChild(), asr=FakeAsr(123, True))
    client = await aiohttp_client(app)
    resp = await client.get("/api/transcribe/available")
    assert (await resp.json()) == {"available": True}


async def test_transcribe_503_when_unavailable(aiohttp_client):
    app = create_app(ModelRegistry(MODELS), FakeChild(), asr=None)
    client = await aiohttp_client(app)
    resp = await client.get("/api/transcribe")
    assert resp.status == 503


async def test_transcribe_ws_proxied_to_asr(aiohttp_client, asr_backend):
    app = create_app(ModelRegistry(MODELS), FakeChild(), asr=FakeAsr(asr_backend.port, True))
    client = await aiohttp_client(app)
    ws = await client.ws_connect("/api/transcribe")
    await ws.send_bytes(b"audio")
    msg = await asyncio.wait_for(ws.receive(), timeout=5)
    assert msg.data == "you-said"
    await ws.close()
