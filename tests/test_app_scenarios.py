import pytest
from supervisor.app import create_app
from supervisor.registry import ModelRegistry
from supervisor.scenarios import ScenarioStore


class _Child:
    state = "ready"
    current_repo = "m"
    error = None
    port = 8999


@pytest.fixture
def client_app():
    reg = ModelRegistry([{"id": "m", "name": "M"}])
    scenarios = ScenarioStore([
        {"id": "a", "name": "A", "description": "d",
         "injections": [{"at_seconds": 1.0, "text": "hi"}]}
    ])
    return create_app(reg, _Child(), scenarios=scenarios)


async def test_scenarios_route_returns_list(aiohttp_client, client_app):
    client = await aiohttp_client(client_app)
    resp = await client.get("/api/scenarios")
    assert resp.status == 200
    data = await resp.json()
    assert data[0]["id"] == "a"


async def test_scenarios_route_empty_when_none(aiohttp_client):
    reg = ModelRegistry([{"id": "m", "name": "M"}])
    client = await aiohttp_client(create_app(reg, _Child()))
    resp = await client.get("/api/scenarios")
    assert resp.status == 200
    assert await resp.json() == []
