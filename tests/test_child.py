import sys
import asyncio
import pytest
from supervisor.child import ChildManager

pytestmark = pytest.mark.asyncio

STUB = "tests/stub_moshi.py"


def builder_ok(delay=0.0):
    def build(repo, port):
        cmd = [sys.executable, STUB, "--port", str(port)]
        if delay:
            cmd += ["--delay", str(delay)]
        return cmd
    return build


def builder_fail(repo, port):
    return [sys.executable, STUB, "--port", str(port), "--fail"]


async def test_switch_reaches_ready():
    mgr = ChildManager(builder_ok(), port=8991, ready_timeout=15)
    try:
        await mgr.switch("repo-a")
        assert mgr.state == "ready"
        assert mgr.current_repo == "repo-a"
    finally:
        await mgr.aclose()


async def test_switch_same_repo_is_noop():
    mgr = ChildManager(builder_ok(), port=8992, ready_timeout=15)
    try:
        await mgr.switch("repo-a")
        first_pid = mgr._proc.pid
        await mgr.switch("repo-a")  # no-op
        assert mgr._proc.pid == first_pid
        assert mgr.state == "ready"
    finally:
        await mgr.aclose()


async def test_switch_to_new_repo_restarts():
    mgr = ChildManager(builder_ok(), port=8993, ready_timeout=15)
    try:
        await mgr.switch("repo-a")
        pid_a = mgr._proc.pid
        await mgr.switch("repo-b")
        assert mgr.current_repo == "repo-b"
        assert mgr._proc.pid != pid_a
        assert mgr.state == "ready"
    finally:
        await mgr.aclose()


async def test_failed_start_sets_error():
    mgr = ChildManager(builder_fail, port=8994, ready_timeout=5)
    try:
        await mgr.switch("repo-x")
        assert mgr.state == "error"
        assert mgr.error
    finally:
        await mgr.aclose()


async def test_request_switch_sets_loading_then_ready():
    mgr = ChildManager(builder_ok(), port=8995, ready_timeout=15)
    try:
        mgr.request_switch("repo-a")
        assert mgr.state == "loading"  # set synchronously, before the task runs
        for _ in range(60):
            if mgr.state != "loading":
                break
            await asyncio.sleep(0.5)
        assert mgr.state == "ready"
        assert mgr.current_repo == "repo-a"
    finally:
        await mgr.aclose()
