import pytest
from supervisor.app import create_app, normalize_system_prompt, MIN_SYSTEM_PROMPT_CHARS
from supervisor.registry import ModelRegistry

MODELS = [{"id": "repo-a", "name": "A"}]
PROMPT = "You work for Dr. Jones's medical office. Record new patient details."


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
def client(aiohttp_client):
    async def _make(system_prompt=None):
        app = create_app(ModelRegistry(MODELS), FakeChild(), system_prompt=system_prompt)
        return await aiohttp_client(app)
    return _make


async def test_unset_reports_unavailable(client):
    cli = await client(None)
    resp = await cli.get("/api/system-prompt")
    assert resp.status == 200
    assert (await resp.json()) == {"available": False, "prompt": None}


async def test_configured_prompt_is_served(client):
    cli = await client(PROMPT)
    assert (await (await cli.get("/api/system-prompt")).json()) == {
        "available": True, "prompt": PROMPT,
    }


async def test_prompt_is_stripped(client):
    cli = await client(f"  \n{PROMPT}\t ")
    assert (await (await cli.get("/api/system-prompt")).json())["prompt"] == PROMPT


@pytest.mark.parametrize("value", ["", "   ", "\n\t ", "x", "12345678", " 12345678 "])
async def test_too_short_reads_as_unset(client, value):
    """Exactly MIN_SYSTEM_PROMPT_CHARS is still too short — the rule is
    "more than", and whitespace doesn't count towards it."""
    cli = await client(value)
    assert (await (await cli.get("/api/system-prompt")).json()) == {
        "available": False, "prompt": None,
    }


def test_threshold_is_exclusive():
    assert normalize_system_prompt("x" * MIN_SYSTEM_PROMPT_CHARS) is None
    assert normalize_system_prompt("x" * (MIN_SYSTEM_PROMPT_CHARS + 1)) == "x" * 9
