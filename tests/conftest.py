"""Local replacements for the pytest-aiohttp fixtures.

pytest-aiohttp had to be held at <1.1 because 1.1+ requires aiohttp>=3.11.0b0,
which contradicts the aiohttp<3.11 that moshi forces on us (see requirements.txt).
That made a test-only package a hostage to a runtime pin.

The fixtures it provided are thin wrappers over aiohttp.test_utils, which ships
inside aiohttp itself -- so we define them here and drop the dependency. Both
fixtures track what they create and tear it down afterwards, which is the only
part of the original worth preserving.

pytest.ini sets asyncio_mode = auto, so pytest-asyncio drives these directly.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture
async def aiohttp_server():
    """Start an aiohttp Application as a real server on an ephemeral port."""
    servers = []

    async def go(app, **kwargs):
        server = TestServer(app)
        await server.start_server(**kwargs)
        servers.append(server)
        return server

    yield go

    for server in reversed(servers):
        await server.close()


@pytest.fixture
async def aiohttp_client():
    """Return a TestClient bound to an Application (or an already-built server)."""
    clients = []

    async def go(app_or_server, **kwargs):
        if isinstance(app_or_server, TestServer):
            client = TestClient(app_or_server, **kwargs)
        else:
            client = TestClient(TestServer(app_or_server), **kwargs)
        await client.start_server()
        clients.append(client)
        return client

    yield go

    for client in reversed(clients):
        await client.close()
