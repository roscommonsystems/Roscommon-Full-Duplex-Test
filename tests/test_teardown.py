import pytest
from supervisor import teardown as t


def test_resolve_prefers_container_id(monkeypatch):
    monkeypatch.setenv("CONTAINER_ID", "42148809")
    monkeypatch.delenv("VAST_INSTANCE_ID", raising=False)
    assert t.resolve_instance_id() == "42148809"


def test_resolve_falls_back_to_label(monkeypatch):
    monkeypatch.delenv("CONTAINER_ID", raising=False)
    monkeypatch.setenv("VAST_CONTAINERLABEL", "C.12345678")
    assert t.resolve_instance_id() == "12345678"


def test_resolve_falls_back_to_explicit_override(monkeypatch):
    monkeypatch.delenv("CONTAINER_ID", raising=False)
    monkeypatch.delenv("VAST_CONTAINERLABEL", raising=False)
    monkeypatch.setenv("VAST_INSTANCE_ID", "999")
    assert t.resolve_instance_id() == "999"


def test_resolve_none_when_nothing_set(monkeypatch):
    for k in ("CONTAINER_ID", "VAST_CONTAINERLABEL", "VAST_INSTANCE_ID"):
        monkeypatch.delenv(k, raising=False)
    assert t.resolve_instance_id() is None


def test_available_true_when_key_and_id(monkeypatch):
    monkeypatch.setenv("VAST_API_KEY", "k")
    monkeypatch.setenv("CONTAINER_ID", "42148809")
    assert t.teardown_available() is True


def test_available_false_without_key(monkeypatch):
    monkeypatch.delenv("VAST_API_KEY", raising=False)
    monkeypatch.setenv("CONTAINER_ID", "42148809")
    assert t.teardown_available() is False


def test_available_false_without_id(monkeypatch):
    monkeypatch.setenv("VAST_API_KEY", "k")
    for k in ("CONTAINER_ID", "VAST_CONTAINERLABEL", "VAST_INSTANCE_ID"):
        monkeypatch.delenv(k, raising=False)
    assert t.teardown_available() is False


class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return str(self._payload)


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def delete(self, url, headers=None):
        self.calls.append((url, headers))
        return self._resp


async def test_destroy_self_success_builds_request():
    sess = _FakeSession(_FakeResp(200, {"success": True, "msg": "ok"}))
    out = await t.destroy_self(sess, "secret", "42148809", base="https://api.test/v0")
    assert out == {"success": True, "msg": "ok"}
    url, headers = sess.calls[0]
    assert url == "https://api.test/v0/instances/42148809/"
    assert headers == {"Authorization": "Bearer secret"}


async def test_destroy_self_raises_on_non_200():
    sess = _FakeSession(_FakeResp(403, "forbidden"))
    with pytest.raises(RuntimeError) as ei:
        await t.destroy_self(sess, "secret", "42148809")
    assert "403" in str(ei.value)
