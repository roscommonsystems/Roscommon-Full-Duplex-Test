import pytest
from supervisor.app import create_app
from supervisor.registry import ModelRegistry
from supervisor import teardown as teardown_mod

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


@pytest.fixture
async def cli(aiohttp_client):
    app = create_app(ModelRegistry(MODELS), FakeChild())
    return await aiohttp_client(app)


async def test_available_reports_true(cli, monkeypatch):
    monkeypatch.setattr(teardown_mod, "teardown_available", lambda: True)
    resp = await cli.get("/api/teardown/available")
    assert resp.status == 200
    assert (await resp.json()) == {"available": True}


async def test_available_reports_false(cli, monkeypatch):
    monkeypatch.setattr(teardown_mod, "teardown_available", lambda: False)
    resp = await cli.get("/api/teardown/available")
    assert (await resp.json()) == {"available": False}


async def test_teardown_503_when_unconfigured(cli, monkeypatch):
    monkeypatch.setattr(teardown_mod, "teardown_available", lambda: False)
    resp = await cli.post("/api/teardown")
    assert resp.status == 503


async def test_teardown_200_on_success(cli, monkeypatch):
    monkeypatch.setattr(teardown_mod, "teardown_available", lambda: True)
    monkeypatch.setattr(teardown_mod, "resolve_instance_id", lambda: "123")
    monkeypatch.setenv("VAST_API_KEY", "k")

    async def fake_destroy(session, api_key, instance_id, base=teardown_mod.VAST_API_BASE):
        assert api_key == "k" and instance_id == "123"
        return {"success": True, "msg": "gone"}

    monkeypatch.setattr(teardown_mod, "destroy_self", fake_destroy)
    resp = await cli.post("/api/teardown")
    assert resp.status == 200
    assert (await resp.json()) == {"success": True, "msg": "gone"}


async def test_teardown_502_on_failure(cli, monkeypatch):
    monkeypatch.setattr(teardown_mod, "teardown_available", lambda: True)
    monkeypatch.setattr(teardown_mod, "resolve_instance_id", lambda: "123")
    monkeypatch.setenv("VAST_API_KEY", "k")

    async def boom(session, api_key, instance_id, base=teardown_mod.VAST_API_BASE):
        raise RuntimeError("vast API 403: forbidden")

    monkeypatch.setattr(teardown_mod, "destroy_self", boom)
    resp = await cli.post("/api/teardown")
    assert resp.status == 502
    assert "403" in (await resp.json())["error"]
