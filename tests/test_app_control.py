import pytest
from supervisor.app import create_app
from supervisor.registry import ModelRegistry

MODELS = [
    {"id": "repo-a", "name": "Model A", "description": "a"},
    {"id": "repo-b", "name": "Model B", "description": "b"},
]


class FakeChild:
    def __init__(self):
        self.state = "ready"
        self.current_repo = "repo-a"
        self.error = None
        self.port = 8999
        self.switched_to = None

    @property
    def is_busy(self):
        return self.state == "loading"

    def request_switch(self, repo):
        self.switched_to = repo
        self.state = "loading"


@pytest.fixture
async def cli(aiohttp_client):
    # create_app stores the child at app["_child"]; tests read it via cli.app["_child"].
    child = FakeChild()
    app = create_app(ModelRegistry(MODELS), child)
    return await aiohttp_client(app)


async def test_models(cli):
    resp = await cli.get("/api/models")
    assert resp.status == 200
    assert await resp.json() == MODELS


async def test_status(cli):
    resp = await cli.get("/api/status")
    assert resp.status == 200
    body = await resp.json()
    assert body["current_repo"] == "repo-a"
    assert body["display_name"] == "Model A"
    assert body["state"] == "ready"


async def test_select_unknown_repo_400(cli):
    resp = await cli.post("/api/select", json={"repo": "nope"})
    assert resp.status == 400


async def test_select_same_ready_200(cli):
    resp = await cli.post("/api/select", json={"repo": "repo-a"})
    assert resp.status == 200
    assert (await resp.json())["state"] == "ready"


async def test_select_new_repo_202(cli):
    resp = await cli.post("/api/select", json={"repo": "repo-b"})
    assert resp.status == 202
    assert (await resp.json())["state"] == "loading"
    assert cli.app["_child"].switched_to == "repo-b"


async def test_select_busy_409(cli):
    cli.app["_child"].state = "loading"
    resp = await cli.post("/api/select", json={"repo": "repo-b"})
    assert resp.status == 409
